"""Dedupe state persisted across runs in state/seen_jobs.json.

The state file is a dict keyed by '{source}:{company}:{id}' with the value
being the ISO timestamp we first saw the job. Each poll loads the file,
diffs incoming jobs against it, marks new ones, then writes back.

The file is committed back to the repo by the GitHub Actions workflow so
state survives across runs.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _key(job: dict) -> str:
    return f"{job.get('source','?')}:{job.get('company','?')}:{job.get('id','?')}"


def load_seen(state_path: str | Path) -> dict[str, str]:
    p = Path(state_path)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_seen(state_path: str | Path, seen: dict[str, str]) -> None:
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)


def diff_and_update(
    incoming: list[dict], seen: dict[str, str]
) -> tuple[list[dict], dict[str, str]]:
    """Return (new_jobs, updated_seen).

    A job is 'new' if its key isn't in seen. Updated seen state has the
    union of all incoming jobs (newly added with current timestamp) AND
    all previously-seen jobs (kept as-is so we don't re-notify on stale
    state).

    Each new job also gets a `first_seen_at` field attached.
    """
    now = datetime.now(timezone.utc).isoformat()
    new_jobs: list[dict] = []
    updated_seen = dict(seen)
    for job in incoming:
        k = _key(job)
        if k in updated_seen:
            job["first_seen_at"] = updated_seen[k]
        else:
            updated_seen[k] = now
            job["first_seen_at"] = now
            new_jobs.append(job)
    return new_jobs, updated_seen


def prune(seen: dict[str, str], current_keys: set[str], grace_days: int = 30) -> dict[str, str]:
    """Remove keys for jobs we haven't seen in `grace_days` polls.

    Without pruning, seen state grows forever. We keep recently-active jobs
    AND any job seen at all in the past 30 days, so closed-and-reopened
    postings still get caught.
    """
    if not seen:
        return {}
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    pruned: dict[str, str] = {}
    for k, ts in seen.items():
        if k in current_keys:
            pruned[k] = ts
            continue
        try:
            seen_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if seen_dt >= cutoff:
            pruned[k] = ts
    return pruned
