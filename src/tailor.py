"""Resume tailoring orchestration.

Entry point called by .github/workflows/tailor.yml. Flow:

  1. Load profile.json and tailor_config.yml
  2. Find the target job in docs/jobs.json by job_key
  3. Fetch the full JD via src/jd_fetch (Phase 1)
  4. Call Sonnet to produce tailored bullets
  5. Call Haiku via src/verify to fact-check each bullet
  6. For each FAIL bullet: retry with a stricter prompt up to N times,
     drop if still failing
  7. Optionally generate a cover letter via Sonnet (same fact-grounding)
  8. Write tailored/{job_key}/{resume.json, cover_letter.md} for the
     render_docx step (Phase 3) to consume

The actual python-docx rendering is Phase 3 — this module stops at the
verified intermediate JSON. That keeps the AI loop testable in isolation
from the document layout.

CLI:
  python -m src.tailor --job-key greenhouse:stripe:12345 [--no-cover-letter]
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from src import jd_fetch, llm_client, tailored_state, verify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tailor")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DOCS_DIR = ROOT / "docs"
TAILORED_DIR = ROOT / "tailored"
# Lives under docs/ so GitHub Pages serves it to the dashboard with no auth.
# The poller's seen_jobs.json stays in state/ — it's internal to v0.
TAILORED_STATE_FILE = DOCS_DIR / "tailored_jobs.json"


TAILOR_SYSTEM_PROMPT = """You are a senior resume editor for technical program management candidates.

You will rewrite the candidate's career bullets to emphasize fit for the target job
description. You may rephrase, reorder, combine, and drop bullets.

You MUST NOT add facts, metrics, technologies, certifications, named systems,
team sizes, or accomplishments that are not present in the source PROFILE.

If the JD asks for a skill or experience that is not in the PROFILE, do NOT invent
or borrow from a similar field. Mark that requirement as a gap in the `gaps` array
of your output.

Return ONLY a JSON object of this exact shape — no prose before or after:

{
  "headline": "one-line summary chosen from profile.headline_variants",
  "bullets": [
    {"achievement_id": "<id from profile.experience[].achievements[].id>",
     "text": "rewritten bullet text"},
    ...
  ],
  "gaps": ["JD asks for X but profile has no matching evidence", ...]
}

Limit to 8-12 bullets total. Order by relevance to the JD."""


COVER_LETTER_SYSTEM_PROMPT = """You are drafting a short cover letter for a job application.

Constraints:
  - Length: 150-200 words, 3 paragraphs.
  - Voice: direct, specific, no corporate filler. No "I am writing to express my keen interest." No "synergy", no "leverage" as a verb, no "fast-paced world".
  - Every concrete claim must trace to PROFILE. No invented metrics, no embellished scope.
  - Open with the specific reason this role and company match the candidate's track record (cite 1 concrete prior achievement).
  - Middle paragraph: 2-3 sentences mapping the candidate's experience to the JD's top 2 requirements.
  - Close: 1 sentence on what conversation the candidate is hoping to have.

Return ONLY the cover letter text. No preamble, no signoff line — just the body."""


@dataclass
class TailorResult:
    headline: str
    bullets: list[dict]   # [{"achievement_id": ..., "text": ..., "verified": bool, "reason": ""}]
    gaps: list[str]
    cover_letter: str
    cost_estimate_usd: float
    dropped_bullet_count: int


def load_profile() -> dict:
    path = CONFIG_DIR / "profile.json"
    if not path.exists():
        raise FileNotFoundError(f"profile.json not found at {path} — author it first")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    import yaml  # lazy: only needed when loading from disk, not in unit tests
    path = CONFIG_DIR / "tailor_config.yml"
    if not path.exists():
        log.warning("tailor_config.yml not found; using defaults")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_jobs() -> list[dict]:
    path = DOCS_DIR / "jobs.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run the poller first")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or []


def find_job(jobs: list[dict], job_key: str) -> dict:
    for j in jobs:
        if f"{j.get('source')}:{j.get('company')}:{j.get('id')}" == job_key:
            return j
    raise KeyError(f"job_key {job_key!r} not in jobs.json")


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[truncated]"


def _parse_tailor_response(text: str) -> tuple[str, list[dict], list[str]]:
    """Parse Sonnet's tailor response into (headline, bullets, gaps)."""
    stripped = verify._strip_to_json(text)
    try:
        parsed = json.loads(stripped)
    except ValueError as e:
        log.warning("tailor: could not parse JSON response: %s", e)
        return "", [], []
    headline = str(parsed.get("headline", ""))
    bullets = [b for b in (parsed.get("bullets") or []) if isinstance(b, dict)]
    gaps = [str(g) for g in (parsed.get("gaps") or [])]
    return headline, bullets, gaps


def _retry_bullet(
    client,
    *,
    profile: dict,
    jd_text: str,
    original_bullet: dict,
    fail_reason: str,
    model: str,
    temperature: float,
) -> dict | None:
    """Ask Sonnet to redo a single bullet under a stricter constraint."""
    user = json.dumps(
        {
            "PROFILE": profile,
            "JD": jd_text,
            "BULLET_TO_FIX": original_bullet,
            "REASON_IT_FAILED": fail_reason,
            "INSTRUCTION": "Rewrite this single bullet. Remove the claim that failed verification. Return ONLY a JSON object {\"achievement_id\": \"...\", \"text\": \"...\"}.",
        },
        ensure_ascii=False,
    )
    resp = llm_client.call(
        client,
        model=model,
        system=TAILOR_SYSTEM_PROMPT,
        user=user,
        max_tokens=400,
        temperature=temperature,
        cache_system=False,
    )
    try:
        parsed = json.loads(verify._strip_to_json(resp.text))
    except ValueError:
        return None
    if isinstance(parsed, dict) and parsed.get("text"):
        return {"achievement_id": parsed.get("achievement_id", ""), "text": str(parsed["text"])}
    return None


def _estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Rough per-call cost in USD. Prices in $/MTok.

    NVIDIA-hosted models (DeepSeek etc.) are free on the NIM tier, so they
    price at $0. Unknown models default to Sonnet pricing (conservative).
    """
    prices = {
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-haiku-4-5": (1.0, 5.0),
    }
    if model.startswith("deepseek") or "/" in model:  # NVIDIA NIM slug, e.g. deepseek-ai/...
        return 0.0
    inp, out = prices.get(model, (3.0, 15.0))
    return (input_tokens * inp + output_tokens * out) / 1_000_000


def tailor(
    job_key: str,
    *,
    profile: dict | None = None,
    cfg: dict | None = None,
    jobs: list[dict] | None = None,
    fake_client: object | None = None,
    jd_text_override: str | None = None,
) -> TailorResult:
    """Run the full tailor pipeline for one job_key.

    Most kwargs are for testability: pass profile/cfg/jobs/fake_client to avoid
    hitting disk or the network. jd_text_override skips the jd_fetch call.
    """
    profile = profile if profile is not None else load_profile()
    cfg = cfg if cfg is not None else load_config()
    jobs = jobs if jobs is not None else load_jobs()
    job = find_job(jobs, job_key)

    models = cfg.get("models", {}) or {}
    tailor_model = models.get("tailor", "claude-sonnet-4-6")
    verify_model = models.get("verify", "claude-haiku-4-5")
    cover_model = models.get("cover_letter", "claude-sonnet-4-6")

    limits = cfg.get("limits", {}) or {}
    max_jd_tokens = int(limits.get("max_jd_tokens", 8000))
    max_output_tokens = int(limits.get("max_output_tokens", 2000))
    temperature = float(limits.get("temperature", 0.4))
    max_retries = int((cfg.get("verification", {}) or {}).get("max_retries_per_bullet", 2))
    cover_letter_enabled = bool((cfg.get("features", {}) or {}).get("cover_letter", True))

    # 1. JD
    jd_text = jd_text_override if jd_text_override is not None else jd_fetch.get_jd_text(job)
    if not jd_text:
        raise RuntimeError(f"could not fetch JD for {job_key}")
    jd_text = _truncate_text(jd_text, max_jd_tokens * 4)  # ~4 chars/token approx

    # 2. Tailor (Sonnet)
    client = llm_client.build_client(fake=fake_client)
    user_payload = json.dumps({"PROFILE": profile, "JD": jd_text}, ensure_ascii=False)
    tailor_resp = llm_client.call(
        client,
        model=tailor_model,
        system=TAILOR_SYSTEM_PROMPT,
        user=user_payload,
        max_tokens=max_output_tokens,
        temperature=temperature,
        cache_system=True,
    )
    headline, bullets, gaps = _parse_tailor_response(tailor_resp.text)
    if not bullets:
        raise RuntimeError("tailor returned zero bullets")

    cost = _estimate_cost(tailor_resp.input_tokens, tailor_resp.output_tokens, tailor_model)

    # 3. Verify (Haiku)
    verdicts = verify.verify_bullets(profile, bullets, client=client, model=verify_model)
    final_bullets: list[dict] = []
    dropped = 0
    for bullet, verdict in zip(bullets, verdicts):
        if verdict.verdict == "PASS":
            final_bullets.append({**bullet, "verified": True, "reason": ""})
            continue
        # FAIL — retry up to max_retries
        current = bullet
        current_reason = verdict.reason
        passed = False
        for attempt in range(max_retries):
            log.info("retry %d for bullet %d (reason: %s)", attempt + 1, verdict.bullet_index, current_reason)
            retried = _retry_bullet(
                client,
                profile=profile,
                jd_text=jd_text,
                original_bullet=current,
                fail_reason=current_reason,
                model=tailor_model,
                temperature=temperature,
            )
            if not retried:
                break
            recheck = verify.verify_bullets(profile, [retried], client=client, model=verify_model)
            if recheck and recheck[0].verdict == "PASS":
                final_bullets.append({**retried, "verified": True, "reason": ""})
                passed = True
                break
            current = retried
            current_reason = recheck[0].reason if recheck else "unknown"
        if not passed:
            log.warning("dropping unverifiable bullet %d after %d retries", verdict.bullet_index, max_retries)
            dropped += 1

    # 4. Cover letter (optional)
    cover_letter = ""
    if cover_letter_enabled:
        cover_user = json.dumps(
            {"PROFILE": profile, "JD": jd_text, "VERIFIED_BULLETS": final_bullets},
            ensure_ascii=False,
        )
        cover_resp = llm_client.call(
            client,
            model=cover_model,
            system=COVER_LETTER_SYSTEM_PROMPT,
            user=cover_user,
            max_tokens=600,
            temperature=temperature,
            cache_system=False,
        )
        cover_letter = cover_resp.text.strip()
        cost += _estimate_cost(cover_resp.input_tokens, cover_resp.output_tokens, cover_model)

    return TailorResult(
        headline=headline,
        bullets=final_bullets,
        gaps=gaps,
        cover_letter=cover_letter,
        cost_estimate_usd=cost,
        dropped_bullet_count=dropped,
    )


def write_output(job_key: str, result: TailorResult, profile: dict | None = None) -> Path:
    """Write resume.json, resume.docx, and cover_letter.md to tailored/{job_key}/.

    Returns the directory. If profile is None, only the JSON + markdown are
    written (the docx render needs the profile to look up company/dates per
    bullet). Workflow callers pass profile so all 3 artifacts ship together.
    """
    safe_key = job_key.replace(":", "_").replace("/", "_")
    out_dir = TAILORED_DIR / safe_key
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "resume.json", "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, indent=2, ensure_ascii=False)
    if result.cover_letter:
        with open(out_dir / "cover_letter.md", "w", encoding="utf-8") as f:
            f.write(result.cover_letter)
    if profile is not None:
        try:
            from src import render_docx
            render_docx.render_resume(profile, asdict(result), out_dir / "resume.docx")
        except Exception as e:
            # Don't lose the JSON if docx rendering fails — log and continue
            log.warning("docx render failed for %s: %s", job_key, e)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-key", required=True, help="e.g., greenhouse:stripe:12345")
    parser.add_argument("--no-cover-letter", action="store_true")
    parser.add_argument("--run-id", default="", help="GitHub Actions run id (for state file)")
    parser.add_argument("--max-per-hour", type=int, default=10,
                        help="refuse to run if more than N tailorings finished in the last hour")
    args = parser.parse_args(argv)

    allowed, current = tailored_state.check_rate_limit(TAILORED_STATE_FILE, max_per_hour=args.max_per_hour)
    if not allowed:
        log.error("rate limit hit: %d tailorings already in the last hour (cap %d)", current, args.max_per_hour)
        return 2

    cfg = load_config()
    if args.no_cover_letter:
        cfg.setdefault("features", {})["cover_letter"] = False

    profile = load_profile()
    result = tailor(args.job_key, profile=profile, cfg=cfg)
    out_dir = write_output(args.job_key, result, profile=profile)
    safe_key = args.job_key.replace(":", "_").replace("/", "_")
    artifact_name = f"tailored-{safe_key}-{args.run_id}" if args.run_id else f"tailored-{safe_key}"
    tailored_state.record(
        TAILORED_STATE_FILE, args.job_key,
        run_id=args.run_id,
        bullets_count=len(result.bullets),
        dropped_count=result.dropped_bullet_count,
        cost_estimate_usd=result.cost_estimate_usd,
        artifact_name=artifact_name,
    )
    log.info(
        "done: %d bullets (%d dropped), cost ~$%.4f, wrote %s",
        len(result.bullets), result.dropped_bullet_count, result.cost_estimate_usd, out_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
