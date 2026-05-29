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
from dataclasses import dataclass

from src import llm_client

log = logging.getLogger(__name__)

VALID_RECOMMENDATIONS = {"strong", "medium", "not"}

FIT_SYSTEM_PROMPT = """You assess how well a candidate fits a specific job, using only their verified career profile and the job description.

Return exactly one recommendation:
- "strong": the candidate's core experience and skills directly match the role's domain, seniority, and primary requirements.
- "medium": the candidate matches seniority and general function but the domain or some key requirements are a partial or adjacent match.
- "not": the role requires a domain, seniority, or skill set the candidate clearly lacks, or it is a different function entirely.

Rules:
- Judge ONLY on evidence in the profile. Never assume skills, domains, or seniority not present.
- Seniority matters: a Director/Principal-level candidate is a weak fit for a first-line IC role and vice versa.
- Domain matters: weight the candidate's actual industries (per the profile) against the role's domain.
- Be honest. "not" is a valid and useful answer — do not inflate.

Return ONLY a JSON object, no prose:
{"recommendation": "strong|medium|not", "reason": "<= 15 words, concrete"}"""


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

    llm = llm_client.build_client(fake=client) if client else llm_client.build_client()
    resp = llm_client.call(
        llm,
        model=model,
        system=FIT_SYSTEM_PROMPT,
        user=user,
        max_tokens=200,
        temperature=0.0,
        cache_system=True,
    )

    try:
        parsed = json.loads(_strip_to_json(resp.text))
    except (ValueError, AttributeError):
        log.warning("fit: non-JSON response for %s: %r", job.get("title"), resp.text[:120])
        return FitVerdict("medium", "could not classify (non-JSON response)")

    rec = str(parsed.get("recommendation", "")).lower().strip()
    reason = str(parsed.get("reason", "")).strip()
    if rec not in VALID_RECOMMENDATIONS:
        log.warning("fit: unexpected recommendation %r for %s", rec, job.get("title"))
        return FitVerdict("medium", reason or "could not classify")
    return FitVerdict(rec, reason)
