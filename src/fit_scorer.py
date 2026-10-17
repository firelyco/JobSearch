"""Salary/privacy helpers and the skill-only scoring compatibility entrypoint."""
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
    excerpts = [(source[m.start():m.start()+240],source[max(0,m.start()-30):m.start()]) for m in re.finditer(label, source, re.I)]
    if quote and quote in source and re.search(label, quote, re.I):
        start=re.search(label,quote,re.I).start()
        excerpts.append((quote[start:],quote[max(0,start-30):start]))
    bands = []
    for section, prefix in excerpts:
        currency_context=prefix+section
        if re.search(r"(?i)\b(?:CAD|AUD|NZD|SGD|HKD|EUR|GBP|hourly|monthly|weekly|per hour|per month|OTE)\b|C\$|A\$|total compensation|total cash|on.target earnings", currency_context):
            continue
        if not re.search(r"(?i)\bUSD\b|US\$|U\.?S\.?\s+(?:base|salary|pay)|United States", currency_context):
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
        upper = re.match(r"\s*(?:USD\s*)?(?:[-\u2013\u2014\ufffd]|to)\s*(?:\$\s*|USD\s*)?" + amount, tail)
        hi = float(upper.group(1).replace(",", "")) * (1000 if upper.group(2) else 1) if upper else lo
        if 10000 <= lo <= hi:
            bands.append((lo, hi))
    if not bands:
        return "unknown"
    if all(lo > floor for lo, hi in bands):
        return "meets_floor"
    return "potentially_meets_floor" if any(hi >= floor for lo, hi in bands) else "below_floor"


def score_fit(profile,job,jd_text,*,client=None,model="claude-haiku-4-5",rules=None,**unused):
    """Compatibility entrypoint for the single private-skill evidence pipeline."""
    if not (rules or {}).get('_fitment_skill'):
        raise RuntimeError('Private fitment skill required; generic scoring is retired')
    from src.evidence_fit import assess
    llm=llm_client.build_client(fake=client) if client else llm_client.build_client()
    return assess(profile,job,jd_text,client=llm,model=model,rules=rules)


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
