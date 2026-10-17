"""Skill-based fit scoring with private evidence, bounded execution and cached results."""
from __future__ import annotations
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from src import fit_scorer, jd_fetch, llm_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("fit")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DOCS_DIR = ROOT / "docs"
JOBS_FILE = DOCS_DIR / "jobs.json"
PROFILE_FILE = CONFIG_DIR / "profile.json"
TAILOR_CONFIG_FILE = CONFIG_DIR / "tailor_config.yml"
FIT_FILE = DOCS_DIR / "fit_scores.json"
# Supplemental private evidence and execution settings. Assessment policy comes
# exclusively from config/fitment_skill.md, loaded from its Actions secret.
FIT_RULES_FILE = CONFIG_DIR / "fit_rules.json"

# Haiku answers in seconds, so 40 jobs fit well inside the job timeout and
# clear a backlog in a few runs (~$0.002/job). Drop back to ~6 on a slow
# provider (the NVIDIA free tier ran 1-2 min/call). Remaining jobs are picked
# up on the next poll-triggered run (incremental, eventually-consistent).
MAX_JOBS_PER_RUN = 40
DEFAULT_FIT_MODEL = "claude-haiku-4-5"


def _fit_model() -> str:
    """Read the fit model slug from tailor_config.yml (falls back to default)."""
    if not TAILOR_CONFIG_FILE.exists():
        return DEFAULT_FIT_MODEL
    try:
        import yaml
        with open(TAILOR_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("models", {}) or {}).get("fit") or DEFAULT_FIT_MODEL
    except Exception:
        return DEFAULT_FIT_MODEL


def _key(job: dict) -> str:
    return f"{job.get('source')}:{job.get('company')}:{job.get('id')}"


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def prune(scores: dict, current_keys: set[str]) -> dict:
    """Drop fit scores for jobs that have aged out of jobs.json."""
    return {k: v for k, v in scores.items() if k in current_keys}


def run(
    *,
    jobs: list[dict] | None = None,
    profile: dict | None = None,
    existing: dict | None = None,
    max_jobs: int = MAX_JOBS_PER_RUN,
    rescore: bool = False,
    fake_client: object | None = None,
    jd_text_for: dict | None = None,
    rules: dict | None = None,
    now: datetime | None = None,
    on_scored=None,
    job_keys: list[str] | None = None,
) -> dict:
    """Score fit for jobs lacking a cached verdict. Returns the updated scores dict.

    Test hooks: pass jobs/profile/existing to avoid disk, fake_client to avoid
    the network, jd_text_for={job_key: jd_text} to avoid jd_fetch HTTP calls.
    on_scored(scores) is called after each new verdict so main() can persist
    progress — a run killed mid-way (slow provider, job timeout) keeps what
    it already paid for.
    """
    jobs = jobs if jobs is not None else load_json(JOBS_FILE, [])
    profile = profile if profile is not None else load_json(PROFILE_FILE, {})
    rules = rules if rules is not None else load_json(FIT_RULES_FILE, {})
    scores = dict(existing if existing is not None else load_json(FIT_FILE, {}))
    stamp = (now or datetime.now(timezone.utc)).isoformat()

    if rules.get('evidence_required'):
        from src.evidence_batch import run as run_evidence
        return run_evidence(jobs=jobs,profile=profile,existing=scores,rules=rules,max_jobs=max_jobs,rescore=rescore,
            fake_client=fake_client,jd_text_for=jd_text_for,stamp=stamp,model=_fit_model(),on_scored=on_scored,job_keys=job_keys)
    raise RuntimeError('Private skill evidence assessment is required; generic scoring is retired')


def save(scores: dict) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    with open(FIT_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, indent=2, ensure_ascii=False, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-only",action="store_true")
    parser.add_argument("--max", type=int, default=MAX_JOBS_PER_RUN)
    parser.add_argument("--rescore", action="store_true", help="re-score every job, ignoring cache")
    parser.add_argument("--job-keys", help="JSON array of specific job keys to score")
    args = parser.parse_args(argv)
    selected_keys = json.loads(args.job_keys) if args.job_keys else None
    if selected_keys is not None and (not isinstance(selected_keys, list) or not all(isinstance(k, str) for k in selected_keys)):
        parser.error("--job-keys must be a JSON array of strings")

    if not PROFILE_FILE.exists():
        log.error("config/profile.json missing — cannot score fit")
        return 1

    if not FIT_RULES_FILE.exists():
        log.info("Using the required private fitment skill")
    from src.fitment_skill import attach
    rules = attach(load_json(FIT_RULES_FILE, {}))
    if args.inventory_only:
        from src.evidence_fit import build_inventory
        from src.private_fit_store import save_private
        save_private({'inventory':build_inventory(load_json(PROFILE_FILE,{}),rules)})
        log.info('Private evidence inventory saved')
        return 0
    policy = {"version": rules.get("fit_policy_version", "")}
    if rules.get("calibration_pending"):policy["calibration_pending"]=True
    if rules.get("fit_api_blocked"):policy["provider_status"]="insufficient_credits"
    if rules.get('evidence_required'):
        from src.evidence_batch import context_hash
        from src.evidence_fit import ENGINE_VERSION,scorer_revision
        policy.update(evidence_required=True,engine_version=ENGINE_VERSION,scorer_revision=scorer_revision(),
            context_hash=context_hash(load_json(PROFILE_FILE,{}),rules,_fit_model()),fitment_skill_hash=rules['fitment_skill_hash'],verdicts=['strong','middle','pass'])
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "fit_policy.json").write_text(json.dumps(policy) + "\n", encoding="utf-8")
    if rules.get("fit_api_blocked"):
        log.warning("Fit scoring is waiting for Anthropic API credits; previous ratings retained")
        return 0
    scores = run(rules=rules, max_jobs=args.max, rescore=args.rescore, on_scored=save, job_keys=selected_keys)
    save(scores)
    log.info("wrote %s (%d entries)", FIT_FILE, len(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
