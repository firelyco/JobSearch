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
import html
import re
from dataclasses import dataclass

from src import llm_client

log = logging.getLogger(__name__)

VALID_RECOMMENDATIONS = {"strong", "medium", "not"}
# Only discipline, domain and guardrails can fail a role outright; level and
# comp are tie-breakers that may only demote.
DEMOTE_ONLY_STEPS = {"level", "comp"}
VALID_STEPS = {"discipline", "domain", "guardrails", "none"} | DEMOTE_ONLY_STEPS

# The method is generic and lives here; the candidate-specific parameters
# (accepted disciplines, domain gates, never-claim list, comp floor) come from
# config/fit_rules.json, which is written from a secret at run time because
# this repo is public. See config/fit_rules.json.example.
FIT_SYSTEM_PROMPT = """You screen job postings for one candidate, using only their verified career profile, their CANDIDATE RULES, and the job description. Honesty is the fixed point: the question underneath every step is whether an honest resume could win this role, or whether a first interview would expose the candidate.

Work through these steps in order. Read past the title every time; titles misstate both the kind of work and the industry.

1. Discipline. From the JD body, decide what work this actually is. If it is one of the candidate's rejected disciplines, or anything else that is not one of their accepted disciplines, it fails.
2. Domain gate. Industry depth gates only when the JD states it as a required qualification AND it is the substance of the role. A domain that appears in the product but not the qualifications does not gate. The company's industry is not the role's domain. Fails if a required domain is one of the candidate's hard domain gates or is otherwise absent from the profile.
3. Level. Judge the ROLE's level from the JD, never the candidate's. A role manages people only if the JD says so (direct reports, "manage a team of", "hire and develop", "lead a team of program managers"). Senior, Staff, Principal, Lead and "Leadership" TPM titles without such language are individual contributor (IC) roles. Use the candidate's target levels and ALLOW_IC_STRONG preference. When ALLOW_IC_STRONG is true, IC status alone does not lower the verdict. Judge senior scope from responsibilities, not merely the title or a minimum-years threshold. Truly junior scope can still lower the verdict.
4. Guardrails. Would an honest resume need a claim on the candidate's never-claim list? If that claim is central to the role it fails; if it is one preferred line it is a stretch to note.
5. Comp and location. Tie-breakers only. Posted comp clearly below the candidate's floor lowers the verdict; it never fails a role alone. For IC roles when IC_STRONG_REQUIRES_COMP is true, Strong requires a disclosed compensation range that can meet the floor: meets_floor if the lower bound meets it, potentially_meets_floor if the range overlaps it, below_floor if the upper bound is below it, and unknown when compensation is not disclosed or not comparable. When STRONG_REQUIRES_BASE_COMP is true, this applies to ALL roles: Strong requires disclosed annual USD base salary strictly above COMP_FLOOR_USD at the LOWER bound. A range that overlaps the floor is provisional, not Strong. Bonus, equity, total compensation and benefits do not count as base salary. Return comp_evidence as an exact contiguous quote from the JD containing the base-salary label and amount/range, or an empty string if absent. Do not assume an offer, invent equity value, or treat an undisclosed range as adequate.

Verdict:
Assess recommendation and step from discipline, domain, level and guardrails ONLY. The application separately enforces the compensation rules using the employer's published salary evidence. Do not set step=comp or lower recommendation due to compensation; return its evidence separately. An employer's Annual Salary or yearly salary disclosure is salary even if it does not use the word base.
- "not" (pass): any hard failure in step 1, 2 or 4.
- "medium" (middle): no hard failure, but a real stretch, e.g. below-target scope, unconfirmed compensation where required, a below-band comp, a preferred guardrail line, or a partial domain match.
- "strong": right discipline, no gate, target level, guardrails intact, and ideally the candidate's differentiators are named requirements.
Judge only on evidence in the profile and the JD. Never assume skills, domains or seniority not present. "not" is a valid and useful answer; do not inflate.

"role_summary" is shown on a public page. It must describe only the ROLE and what it asks for, never the candidate: no years of experience, past titles, employers, domains the candidate has, or comp floor. Start it with the deciding step, e.g. "Discipline: this is product management, not program management" or "Domain gate: requires clinical trial operations depth" or "Strong: leads a team of TPMs on an AI platform".

Return ONLY a JSON object, no prose:
{"recommendation": "strong|medium|not", "step": "discipline|domain|level|guardrails|comp|none", "role_level": "ic|people_manager|unknown", "comp_status": "meets_floor|potentially_meets_floor|below_floor|unknown", "comp_evidence": "exact annual salary or base salary quote from JD or empty", "role_summary": "<= 20 words about the role"}
"step" is the step that decided the verdict ("none" for a clean strong). "role_level" is from the JD alone: people_manager only if the JD describes managing people."""


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
    step: str = "none"    # the deciding step, one of VALID_STEPS


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


def redact_history(reason: str, profile: dict, rules: dict | None = None) -> str:
    """Replace the candidate's employer and school names and comp floor in a reason.

    fit_scores.json is served on the public dashboard, and the prompt's
    "describe the role" instruction isn't always followed, so enforce it.
    """
    names = [exp.get("company", "") for exp in profile.get("experience", []) or []]
    names += [edu.get("school", "") for edu in profile.get("education", []) or []]
    for name in sorted({n for n in names if n and len(n) > 2}, key=len, reverse=True):
        reason = re.sub(re.escape(name), "prior employer", reason, flags=re.IGNORECASE)
        # Also catch the first word alone ("Acme" for "Acme Robotics").
        first = name.split()[0]
        if len(first) > 3 and first.lower() != name.lower():
            reason = re.sub(rf"\b{re.escape(first)}\b", "prior employer", reason, flags=re.IGNORECASE)
    floor = (rules or {}).get("comp_floor_usd")
    if isinstance(floor, (int, float)) and floor >= 1000:
        k = int(floor) // 1000
        # e.g. "$150K", "150k", "150,000" -> "[redacted]" (backstop; the prompt forbids it)
        reason = re.sub(rf"\$?\b{k}(?:,?000\b|\s?[kK]\b)", "[redacted]", reason)
    return reason


def _strip_to_json(s: str) -> str:
    if not s:
        return "{}"
    first = s.find("{")
    last = s.rfind("}")
    if first < 0 or last < 0 or last < first:
        return s
    return s[first : last + 1]



def salary_text(text: str) -> str:
    """Normalize employer HTML and model evidence into comparable plain text."""
    text = html.unescape(html.unescape(str(text or "")))
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def verified_base_comp_status(jd_text: str, evidence: str, floor: float) -> str:
    """Read literal salary bands; reject total compensation, ambiguous currency,
    nonannual pay, invented evidence and ranges not clearing the private floor.
    Annual/yearly salary labels mean salary, not a total compensation package.
    """
    source = salary_text(jd_text)
    quote = salary_text(evidence)
    label = r"base\s+(?:salary|pay|compensation)|annual\s+salary|(?:estimated\s+)?yearly\s+salary"
    # Prefer the employer's salary section over a model-selected excerpt.
    excerpts = [source[m.start():m.start()+240] for m in re.finditer(label, source, re.I)]
    if quote and quote in source and re.search(label, quote, re.I):
        excerpts.append(quote)
    bands = []
    for section in excerpts:
        if re.search(r"(?i)\b(?:CAD|AUD|NZD|SGD|HKD|EUR|GBP|hourly|monthly|weekly|per hour|per month|OTE)\b|C\$|A\$|total compensation|total cash|on.target earnings", section):
            continue
        if not re.search(r"(?i)\bUSD\b|US\$|U\.?S\.?\s+(?:base|salary|pay)|United States", section):
            continue
        amount = r"(\d{2,3}(?:,\d{3})+|\d{5,7}|\d{2,3}(?:\.\d+)?)\s*([kK])?"
        # A salary label is followed by a literal pay band, not unrelated
        # company revenue or an amount from a subsequent benefit paragraph.
        m = re.search(r"(?:\$\s*|\bUSD\s+)" + amount, section)
        if not m:
            m = re.search(amount + r"\s+USD\b", section, re.I)
        if not m:
            continue
        lo = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        tail = section[m.end():]
        tail = re.sub(r"^\s*(?:/\s*year|per\s+year|annually)\b", " ", tail, flags=re.I)
        upper = re.match(r"\s*(?:USD\s*)?(?:[-–—�]|to)\s*(?:\$\s*|USD\s*)?" + amount, tail)
        hi = float(upper.group(1).replace(",", "")) * (1000 if upper.group(2) else 1) if upper else lo
        if 10000 <= lo <= hi:
            bands.append((lo, hi))
    if not bands:
        return "unknown"
    if all(lo > floor for lo, hi in bands):
        return "meets_floor"
    return "potentially_meets_floor" if any(hi > floor for lo, hi in bands) else "below_floor"


def score_fit(
    profile: dict,
    job: dict,
    jd_text: str,
    *,
    client: object | None = None,
    model: str = "claude-haiku-4-5",
    condensed: str | None = None,
    max_jd_chars: int = 16000,
    rules: dict | None = None,
) -> FitVerdict:
    """Assess fit for one job. Returns a FitVerdict.

    `condensed` lets fit.py pass a pre-built profile summary so we don't
    re-condense the same profile for every job. On any parse failure the
    verdict defaults to "medium" with an explanatory reason (cached, so we
    don't retry forever).
    """
    if (rules or {}).get("evidence_required"):
        from src.evidence_fit import assess
        llm = llm_client.build_client(fake=client) if client else llm_client.build_client()
        return assess(profile,job,jd_text,client=llm,model=model,rules=rules)
    profile_text = condensed if condensed is not None else condense_profile(profile)
    jd = (jd_text or "").strip()
    if len(jd) > max_jd_chars:
        # Salary sections often follow long benefit/legal sections. Preserve
        # their evidence rather than treating a truncated salary as undisclosed.
        matches = list(re.finditer(r"(?i)compensation range|base salary|salary range|total cash range|\$\s*\d{2}", jd))
        excerpts = [jd[max(0, match.start() - 60):match.start() + 650] for match in matches]
        compensation = "\n".join(dict.fromkeys(excerpts))[:min(1600, max_jd_chars // 3)]
        budget = max_jd_chars - len(compensation)
        jd = jd[:budget] + "\n[truncated]" + ("\nCompensation excerpts from this posting:\n" + compensation if compensation else "")

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
    if (rules or {}).get("strong_requires_base_comp"):
        system += "\nFinal output instruction: recommendation and step assess role fit only. Never use step=comp; the application checks salary separately. Annual Salary and yearly salary are valid salary labels."


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
    step = str(parsed.get("step", "none")).lower().strip()
    if step not in VALID_STEPS:
        step = "none"
    raw = str(parsed.get("role_summary") or parsed.get("reason") or "").strip()
    reason = public_summary(redact_history(raw, profile, rules), step)
    if rec not in VALID_RECOMMENDATIONS:
        log.warning("fit: unexpected recommendation %r for %s", rec, job.get("title"))
        return FitVerdict("medium", reason or "could not classify", step)
    if rec == "not" and step in DEMOTE_ONLY_STEPS:
        # Level and comp only demote; a pass needs a discipline, domain or
        # guardrail failure. Enforced here because the model sometimes stacks
        # two demotions into a pass.
        log.info("fit: %s pass on %r downgraded to medium", job.get("title"), step)
        rec = "medium"
    role_level = str(parsed.get("role_level", "unknown")).lower().strip()
    if rec == "strong" and role_level == "ic" and not (rules or {}).get("allow_ic_strong", False):
        # The model tends to judge the candidate's level instead of the
        # role's; an individual-contributor role caps at medium.
        log.info("fit: %s strong on an IC role capped at medium", job.get("title"))
        rec, step = "medium", "level"
        reason = f"Level: individual contributor role. {reason}".strip()
    strict_base = (rules or {}).get("strong_requires_base_comp", False)
    requires_comp = strict_base or (role_level == "ic" and (rules or {}).get("allow_ic_strong") and (rules or {}).get("ic_strong_requires_comp"))
    if rec == "strong" and requires_comp:
        comp_status = parsed.get("comp_status", "unknown")
        if strict_base:
            comp_status = verified_base_comp_status(jd_text, parsed.get("comp_evidence", ""), float((rules or {}).get("comp_floor_usd", 0)))
        allowed = {"meets_floor"} if strict_base else {"meets_floor", "potentially_meets_floor"}
        if comp_status not in allowed:
            rec, step = "medium", "comp"
            explanation = {
                "below_floor": "Comp: posted base salary does not clear the required threshold.",
                "potentially_meets_floor": "Comp: posted range requires offer confirmation.",
            }.get(comp_status, "Comp: annual USD base salary confirmation needed.")
            reason = f"{explanation} {reason}".strip()
        elif comp_status == "potentially_meets_floor":
            reason = f"{reason} Confirm compensation within the posted range.".strip()
    return FitVerdict(rec, reason, step)


# Text that describes the candidate rather than the role. The summary is
# public, so one that slips through is replaced rather than published.
_CANDIDATE_TALK_RE = re.compile(
    r"\b\d+\+?\s*(?:years|yrs)\b|\bcandidate\b|\bprofile\b|\bguardrails?\b"
    r"|\bprior employer\b|\b(?:my|his|her|their) (?:experience|background)\b"
    r"|\b(?:org|organization)[- ]builder\b|\bbuilt (?:the )?(?:tpm|program)",
    re.IGNORECASE,
)


def public_summary(text: str, step: str) -> str:
    if text and not _CANDIDATE_TALK_RE.search(text):
        return text
    label = {"none": "Strong", "comp": "Comp"}.get(step, step.capitalize())
    return f"{label}: details withheld from the public page" if text else ""
