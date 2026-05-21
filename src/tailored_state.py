"""Persistent state of which jobs have been tailored.

Lives in state/tailored_jobs.json, parallel to seen_jobs.json (which is
v0's polling state). Two reasons for a separate file:
  1. The v0 poll workflow doesn't need this file and shouldn't touch it.
  2. Schema is different: tailored entries are objects with artifact
     metadata, not flat timestamps.

Schema:
  {
    "greenhouse:stripe:12345": {
      "tailored_at": "2026-05-20T18:45:00Z",
      "run_id": "1234567890",
      "artifact_name": "tailored-greenhouse_stripe_12345-1234567890",
      "bullets_count": 8,
      "dropped_count": 1,
      "cost_estimate_usd": 0.0567
    }
  }

Read by the dashboard (via fetch) to decide whether to show "Tailor"
or "Download" on each job row.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def load(state_path: str | Path) -> dict[str, dict]:
    p = Path(state_path)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state_path: str | Path, state: dict[str, dict]) -> None:
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def record(
    state_path: str | Path,
    job_key: str,
    *,
    run_id: str,
    bullets_count: int,
    dropped_count: int,
    cost_estimate_usd: float,
    artifact_name: str = "",
    now: datetime | None = None,
) -> dict:
    """Add or overwrite the entry for job_key, persist, return the new entry."""
    state = load(state_path)
    entry = {
        "tailored_at": (now or datetime.now(timezone.utc)).isoformat(),
        "run_id": run_id,
        "artifact_name": artifact_name,
        "bullets_count": bullets_count,
        "dropped_count": dropped_count,
        "cost_estimate_usd": round(cost_estimate_usd, 6),
    }
    state[job_key] = entry
    save(state_path, state)
    return entry


def count_in_window(state: dict[str, dict], *, hours: int, now: datetime | None = None) -> int:
    """How many tailorings landed in the last `hours` hours.

    Used by the workflow to refuse runaway clicks (Risk 6 mitigation).
    Bad / unparseable timestamps are skipped, not counted.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    count = 0
    for entry in state.values():
        if not isinstance(entry, dict):
            continue
        ts = entry.get("tailored_at", "")
        try:
            entry_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if entry_dt >= cutoff:
            count += 1
    return count


def check_rate_limit(state_path: str | Path, *, max_per_hour: int = 10) -> tuple[bool, int]:
    """Return (allowed, current_count). False if at/over the cap.

    The workflow calls this BEFORE running tailor.py and exits non-zero
    when allowed=False, so we never spend Anthropic credit on a refused run.
    """
    state = load(state_path)
    count = count_in_window(state, hours=1)
    return (count < max_per_hour, count)
