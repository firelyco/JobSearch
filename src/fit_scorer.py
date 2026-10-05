"""Haiku-based profile-fit assessment.

Given the candidate's profile and a job (title + JD body), classify how well
the candidate's actual experience and skills fit the role:

  - "strong" : core experience + skills directly match domain, seniority, and
               primary requirements
  - "medium" : seniority and function (TPM) match, but domain or some key
               requirements are partial/adjacent
  - "not"    : role needs a domain, seniority, or skill set the candidate
               clearly lacks, or is a different function

This is distinct from scorer.py (which matches the role TYPE against
role_config.yml and is identical for any candidate). Fit scoring is about
THIS candidate's background vs THIS job.

Cheap by design: Haiku, a condensed profile (not the full JSON), and JD
truncated to a few thousand chars. ~$0.001-0.003 per job. Cached per job_key
by fit.py so each job is scored once.
"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass

from src import llm_client

log = logging.getLogger(__name__)

VALID_RECOMMENDATIONS = {"strong", "medium", "not"}

# The method is generic and lives here; the candidate-specific parameters
# (accepted disciplines, domain gates, never-claim list, comp floor) come from
# config/fit_rules.json, which is written from a secret at run time because
# this repo is public. See config/fit_rules.json.example.
FIT_SYSTEM_PROMPT = """You screen job postings for one candidate, using only their verified career profile, their CANDIDATE RULES, and the job description. Honesty is the fixed point: the question underneath every step is whether an honest resume could win this role, or whether a first interview would expose the candidate.

Work through these steps in order. Read past the title every time; titles misstate both the kind of work and the industry.

1. Discipline. From the JD body, decide what work this actually is. If it is one of the candidate's rejected disciplines, or anything else that is not one of their accepted disciplines, it fails.
2. Domain gate. Industry depth gates only when the JD states it as a required qualification AND it is the substance of the role. A domain that appears in the product but not the qualifications does not gate. The company's industry is not the role's domain. Fails if a required domain is one of the candidate's hard domain gates or is otherwise absent from the profile.
3. Level. Compare the role's level and shape (people leader vs individual contributor) with the candidate's targets. This is a flag, not a failure: an IC role or one slightly below target lowers the verdict, it does not fail it.
4. Guardrails. Would an honest resume need a claim on the candidate's never-claim list? If that claim is central to the role it fails; if it is one preferred line it is a stretch to note.
5. Comp and location. Tie-breakers only. Posted comp clearly below the candidate's floor lowers the verdict; it never fails a role alone.

Verdict:
- "not" (pass): any hard failure in step 1, 2 or 4.
- "medium" (middle): no hard failure, but a real stretch, e.g. IC level, a below-band comp, a preferred guardrail line, or a partial domain match.
- "strong": right discipline, no gate, target level, guardrails intact, and ideally the candidate's differentiators are named requirements.
Judge only on evidence in the profile and the JD. Never assume skills, domains or seniority not present. "not" is a valid and useful answer; do not inflate.

The reason is shown on a public page, so describe the ROLE. Never mention the candidate's employers, schools, titles or history. Start it with the deciding step, e.g. "Discipline: this is product management, not program management" or "Domain gate: requires clinical trial operations depth" or "Strong: platform program org leadership is the core ask".

Return ONLY a JSON object, no prose:
{"recommendation": "strong|medium|not", "reason": "<= 20 words"}"""


def render_rules(rules: dict | None) -> str:
    """Render candidate fit rules (fit_rules.json) as prompt text.

    Generic over keys so the rules file can evolve without code changes:
    lists become bullets, scalars become one line.
    """
    if not rules:
        return ""
    lines = ["CANDIDATE RULES:"]
    for key, value in rules.items():
        if key.startswith("_"):
            continue
        label = key.replace("_", " ").upper()
        if isinstance(value, list):
            lines.append(f"{label}:")
            lines.extend(f"  - {v}" for v in value)
        else:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


@dataclass
class FitVerdict:
    recommendation: str   # "strong" | "medium" | "not"
    reason: str


def condense_profile(profile: dict) -> str:
    """Compact text representation of the profile for the prompt.

    Drops verbose `details` text; keeps headline, per-role
    company/title/summary/themes, the flattened skill list, and target
    industries. Keeps token cost low without losing the signal that matters
    for a fit judgment.
    """
    person = profile.get("person", {}) or {}
    headline = (person.get("headline_variants", {}) or {}).get("default", "")
    lines = [f"HEADLINE: {headline}", "", "EXPERIENCE:"]
    for exp in profile.get("experience", []) or []:
        company = exp.get("company", "")
        title = exp.get("title", "")
        start = exp.get("start", "")
        end = exp.get("end") or "Present"
        themes = ", ".join(exp.get("themes", []) or [])
        lines.append(f"- {title} at {company} ({start}-{end}) [{themes}]")
        for ach in exp.get("achievements", []) or []:
            summary = ach.get("summary", "")
            if summary:
                lines.append(f"    * {summary}")
    skills = profile.get("skills", {}) or {}
    flat_skills = sorted({s for items in skills.values() for s in (items or [])})
    lines.append("")
    lines.append("SKILLS: " + "; ".join(flat_skills))
    prefs = profile.get("preferences", {}) or {}
    industries = ", ".join(prefs.get("industries_of_interest", []) or [])
    if industries:
        lines.append(f"INDUSTRIES OF INTEREST: {industries}")
    return "\n".join(lines)


def redact_history(reason: str, profile: dict) -> str:
    """Replace the candidate's employer and school names in a reason.

    fit_scores.json is served on the public dashboard, and the prompt's
    "describe the role" instruction isn't always followed, so enforce it.
    """
    names = [exp.get("company", "") for exp in profile.get("experience", []) or []]
    names += [edu.get("school", "") for edu in profile.get("education", []) or []]
    for name in sorted({n for n in names if n and len(n) > 2}, key=len, reverse=True):
        reason = re.sub(re.escape(name), "prior employer", reason, flags=re.IGNORECASE)
        # Also catch the first word alone ("Berkshire" for "Berkshire Grey").
        first = name.split()[0]
        if len(first) > 3 and first.lower() != name.lower():
            reason = re.sub(rf"\b{re.escape(first)}\b", "prior employer", reason, flags=re.IGNORECASE)
    return reason


def _strip_to_json(s: str) -> str:
    if not s:
        return "{}"
    first = s.find("{")
    last = s.rfind("}")
    if first < 0 or last < 0 or last < first:
        return s
    return s[first : last + 1]


def score_fit(
    profile: dict,
    job: dict,
    jd_text: str,
    *,
    client: object | None = None,
    model: str = "claude-haiku-4-5",
    condensed: str | None = None,
    max_jd_chars: int = 6000,
    rules: dict | None = None,
) -> FitVerdict:
    """Assess fit for one job. Returns a FitVerdict.

    `condensed` lets fit.py pass a pre-built profile summary so we don't
    re-condense the same profile for every job. On any parse failure the
    verdict defaults to "medium" with an explanatory reason (cached, so we
    don't retry forever).
    """
    profile_text = condensed if condensed is not None else condense_profile(profile)
    jd = (jd_text or "").strip()
    if len(jd) > max_jd_chars:
        jd = jd[:max_jd_chars] + "\n[truncated]"

    user = (
        f"{profile_text}\n\n"
        f"JOB TITLE: {job.get('title','')}\n"
        f"COMPANY: {job.get('company','')}\n"
        f"LOCATION: {job.get('location','')}\n\n"
        f"JOB DESCRIPTION:\n{jd if jd else '(no JD body available — judge on title alone, lower confidence)'}"
    )

    # Rules are constant for a run, so they go in the cached system prompt.
    rules_text = render_rules(rules)
    system = f"{FIT_SYSTEM_PROMPT}\n\n{rules_text}" if rules_text else FIT_SYSTEM_PROMPT

    llm = llm_client.build_client(fake=client) if client else llm_client.build_client()
    resp = llm_client.call(
        llm,
        model=model,
        system=system,
        user=user,
        max_tokens=300,
        temperature=0.0,
        cache_system=True,
    )

    try:
        parsed = json.loads(_strip_to_json(resp.text))
    except (ValueError, AttributeError):
        log.warning("fit: non-JSON response for %s: %r", job.get("title"), resp.text[:120])
        return FitVerdict("medium", "could not classify (non-JSON response)")

    rec = str(parsed.get("recommendation", "")).lower().strip()
    reason = redact_history(str(parsed.get("reason", "")).strip(), profile)
    if rec not in VALID_RECOMMENDATIONS:
        log.warning("fit: unexpected recommendation %r for %s", rec, job.get("title"))
        return FitVerdict("medium", reason or "could not classify")
    return FitVerdict(rec, reason)
