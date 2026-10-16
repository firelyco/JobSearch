"""Profile-fit scoring entrypoint, run by .github/workflows/fit.yml.

Flow:
  1. Load docs/jobs.json, config/profile.json, docs/fit_scores.json
  2. Prune fit scores for jobs no longer in jobs.json
  3. Find jobs without a cached fit score (incremental — cheap steady state)
  4. For each (capped at MAX_JOBS_PER_RUN), fetch the JD and score with Haiku
  5. Write docs/fit_scores.json for the dashboard to read

State shape (docs/fit_scores.json):
  {
    "greenhouse:stripe:12345": {
      "recommendation": "strong",
      "reason": "20y TPM + fintech AI directly matches platform role",
      "scored_at": "2026-05-21T22:00:00+00:00"
    }
  }

CLI:
  python -m src.fit [--max N] [--rescore]
"""
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
# Candidate-specific screening rules. Gitignored (the repo is public); fit.yml
# writes it from the FIT_RULES_JSON secret. Optional: without it the scorer
# falls back to the generic method alone.
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
    current_keys = {_key(j) for j in jobs}
    scores = prune(scores, current_keys)

    version = rules.get("fit_policy_version")
    to_score = [j for j in jobs if (job_keys is None or _key(j) in job_keys)
                and (rescore or _key(j) not in scores or version and scores[_key(j)].get("policy_version") != version)]
    if not to_score:
        log.info("no jobs need fit scoring (%d already cached)", len(scores))
        return scores

    # Recheck previously Strong roles first when private criteria change.
    # Preserve stable order within groups and existing cache identities.
    to_score.sort(key=lambda j: not (scores.get(_key(j), {}).get("recommendation") == "strong" or scores.get(_key(j), {}).get("step") == "comp"))
    capped = to_score[:max_jobs]
    if len(to_score) > max_jobs:
        log.warning("capping fit scoring at %d/%d jobs this run; rest next cycle",
                    max_jobs, len(to_score))

    client = llm_client.build_client(fake=fake_client)
    condensed = fit_scorer.condense_profile(profile)
    model = _fit_model()

    scored_count = 0
    for job in capped:
        k = _key(job)
        if jd_text_for is not None:
            jd_text = jd_text_for.get(k, "")
        else:
            try:
                jd_text = jd_fetch.get_jd_text(job)
            except Exception as e:
                log.warning("jd fetch failed for %s: %s", k, e)
                jd_text = ""
        try:
            verdict = fit_scorer.score_fit(
                profile, job, jd_text, client=client, condensed=condensed, model=model,
                rules=rules,
            )
        except Exception as e:
            # A slow/flaky provider (e.g. NVIDIA free-tier timeouts) must not
            # crash the whole run and lose already-scored jobs. Skip this one;
            # it stays uncached and gets retried next cycle.
            log.warning("fit scoring failed for %s: %s — skipping, will retry next run", k, e)
            continue
        scores[k] = {
            "recommendation": verdict.recommendation,
            "reason": verdict.reason,
            "step": verdict.step,
            "scored_at": stamp,
            **({"policy_version": version} if version else {}),
        }
        scored_count += 1
        if on_scored is not None:
            on_scored(scores)
        log.info("fit %-7s %s | %s", verdict.recommendation, k, verdict.reason)

    log.info("scored %d new jobs; %d total cached", scored_count, len(scores))
    return scores


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
        log.warning("config/fit_rules.json missing — scoring with the generic method only")
    rules = load_json(FIT_RULES_FILE, {})
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
            context_hash=context_hash(load_json(PROFILE_FILE,{}),rules,_fit_model()))
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
