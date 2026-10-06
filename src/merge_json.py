"""Merge two versions of a keyed JSON state file after a git rebase conflict.

fit.yml and tailor.yml each commit one dict-of-entries file
(docs/fit_scores.json, docs/tailored_jobs.json). When two runs push close
together, the rebase conflicts even though they usually touched different
keys. Keeping either side whole would drop the other run's entries, so
union them by key, with this run's entries winning on overlap.

CLI (used by the workflows):
    python -m src.merge_json <path-with-upstream-version> <this-run-copy>
Writes the merged result to <path-with-upstream-version>.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def merge(upstream: dict, ours: dict) -> dict:
    merged = dict(upstream)
    merged.update(ours)
    return merged


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print("usage: python -m src.merge_json <upstream-path> <ours-path>", file=sys.stderr)
        return 2
    target, ours_path = Path(args[0]), Path(args[1])
    upstream = json.loads(target.read_text(encoding="utf-8"))
    ours = json.loads(ours_path.read_text(encoding="utf-8"))
    if not isinstance(upstream, dict) or not isinstance(ours, dict):
        print("both files must hold a JSON object", file=sys.stderr)
        return 1
    target.write_text(
        json.dumps(merge(upstream, ours), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
