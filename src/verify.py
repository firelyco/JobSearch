"""Haiku-based fact verification.

Given a tailored resume (list of bullets) and the source profile JSON,
verifies that every claim in every bullet traces to a specific field in
the profile. Returns per-bullet pass/fail with a reason. The tailor loop
uses this to decide whether to retry, drop, or keep each bullet.

Why a separate verifier instead of trusting Sonnet's first output:
  - Sonnet is incentivized to be helpful; "helpful" can drift into
    embellishment when a JD asks for skills not in the profile.
  - Haiku running on the OUTPUT of Sonnet is a different prompt with
    different optimization pressure — it's looking for hallucinations,
    not authoring prose. Adversarial-by-design.
  - Cost: ~5x cheaper than Sonnet, runs on ~2KB of context per call.

This is the most security-critical artifact in v0.2 (Risk 8 in the PRD).
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

from src import llm_client

log = logging.getLogger(__name__)


VERIFY_SYSTEM_PROMPT = """You are a fact-checker for a job-application resume.

You will be given:
  1. PROFILE: a JSON object representing the candidate's verified career data
     (experience entries with achievements, metrics, technologies, skills, education).
  2. BULLETS: an array of tailored resume bullets, each with the achievement_id
     it claims to be derived from.

For each bullet, decide PASS or FAIL.

PASS means every factual claim — companies, dates, titles, metrics, technologies,
named projects, named regulations, named systems — is supported by an explicit
field in PROFILE under the referenced achievement_id (or under the candidate's
top-level skills/education sections, as appropriate).

FAIL means the bullet contains at least one claim not supported by PROFILE:
  - A metric (number, percentage, dollar amount, team size, timeline) that is
    not in the achievement's `metrics` array or context.
  - A technology, tool, or framework not in the achievement's `tech` array or
    the candidate's `skills` section.
  - A named system, regulation, product, or company not mentioned in PROFILE.
  - A leadership scope (e.g., "led 50 engineers") not supported by PROFILE.
  - Placeholder text like "[REAL DETAILS HERE]" or "[real metric]" — always FAIL.

Rephrasing, reordering, and emphasis changes are allowed. Generic strong verbs
("led", "drove", "delivered", "scaled") are allowed even if the profile uses
different wording, as long as the underlying claim is supported.

Return ONLY a JSON object of this exact shape — no prose before or after:

{
  "results": [
    {"bullet_index": 0, "verdict": "PASS"},
    {"bullet_index": 1, "verdict": "FAIL", "reason": "claims 50% reduction but profile metrics show 30%"}
  ]
}

If the input is malformed, return {"results": []}."""


@dataclass
class BulletVerdict:
    bullet_index: int
    verdict: str  # "PASS" or "FAIL"
    reason: str = ""


def verify_bullets(
    profile: dict,
    bullets: list[dict],
    *,
    client: object | None = None,
    model: str = "claude-haiku-4-5",
) -> list[BulletVerdict]:
    """Run the verifier on a list of tailored bullets.

    Each bullet should be a dict like {"achievement_id": "...", "text": "..."}.
    Returns a list of BulletVerdict, one per input bullet. Missing verdicts in
    the model's response are treated as FAIL with reason "no verdict returned".
    """
    if not bullets:
        return []

    user_payload = json.dumps(
        {"PROFILE": profile, "BULLETS": bullets},
        ensure_ascii=False,
    )
    llm = llm_client.build_client(fake=client) if client else llm_client.build_client()
    resp = llm_client.call(
        llm,
        model=model,
        system=VERIFY_SYSTEM_PROMPT,
        user=user_payload,
        max_tokens=1500,
        temperature=0.0,
        cache_system=True,
    )

    try:
        parsed = json.loads(_strip_to_json(resp.text))
    except (ValueError, AttributeError) as e:
        log.warning("verify: could not parse response as JSON: %s; raw=%r", e, resp.text[:200])
        return [BulletVerdict(i, "FAIL", reason="verifier returned non-JSON") for i in range(len(bullets))]

    raw_results = parsed.get("results", []) if isinstance(parsed, dict) else []
    by_index: dict[int, dict] = {}
    for r in raw_results:
        if isinstance(r, dict) and "bullet_index" in r:
            try:
                idx = int(r["bullet_index"])
                by_index[idx] = r
            except (ValueError, TypeError):
                continue

    verdicts: list[BulletVerdict] = []
    for i in range(len(bullets)):
        r = by_index.get(i)
        if r is None:
            verdicts.append(BulletVerdict(i, "FAIL", reason="no verdict returned"))
            continue
        verdict = "PASS" if str(r.get("verdict", "")).upper() == "PASS" else "FAIL"
        reason = str(r.get("reason", ""))
        verdicts.append(BulletVerdict(i, verdict, reason=reason))
    return verdicts


def _strip_to_json(s: str) -> str:
    """Trim any prose around a JSON object. Models sometimes prepend a sentence."""
    if not s:
        return "{}"
    first = s.find("{")
    last = s.rfind("}")
    if first < 0 or last < 0 or last < first:
        return s
    return s[first : last + 1]
