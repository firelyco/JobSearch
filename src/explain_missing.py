"""Diagnose a missing posting without altering job/application state.

python -m src.explain_missing --url https://careers.example.com/job/123
Use --learn to add a supported ATS board found on that public posting.
"""
import argparse
import json
from pathlib import Path
import requests
import yaml
from src import discovery, scorer

ROOT = Path(__file__).resolve().parents[1]

def explain(url, learn=False):
    normalized = discovery.canonical_url(url)
    if not normalized: return {"stage": "invalid_url", "message": "Use a public HTTPS job-posting URL."}
    jobs = json.loads((ROOT / "docs/jobs.json").read_text(encoding="utf-8"))
    matches = [j for j in jobs if discovery.canonical_url(j.get("url", "")) == normalized]
    if matches: return {"stage": "published", "message": "Present in the published snapshot. Check dashboard role, source, location, fit and applied filters.", "matches": matches}
    board = discovery.board_from_url(normalized)
    learned = [board] if board else []
    candidates = []
    if not board:
        host = discovery.urlparse(normalized).hostname or ""
        if not discovery.re.match(r"^(careers|jobs)\.[a-z0-9-]+\.[a-z.]+$", host):
            return {"stage": "unsupported_page", "message": "Not in the snapshot. Supply the employer's careers URL or supported ATS link."}
        response = requests.get(normalized, timeout=20, allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200: return {"stage": "redirect", "message": "Use the final employer posting URL."}
        candidates, learned = discovery.jobs_from_page(response.text, normalized)
    role = yaml.safe_load((ROOT / "config/role_config.yml").read_text(encoding="utf-8"))
    checks = [{"title": j["title"], "score": scorer.score_job(j, role)[0], "reasons": scorer.score_job(j, role)[1]} for j in candidates]
    if learn and learned:
        path = ROOT / "state/discovered_boards.json"
        prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(list({discovery.board_key(b): b for b in prior + learned}.values()), indent=2), encoding="utf-8")
    return {"stage": "not_discovered" if learned else "requires_inspection", "message": "Not in the current snapshot; employer boards found below can be monitored on subsequent polls.", "boards": learned, "role_checks": checks, "learned": bool(learn and learned)}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--url", required=True); parser.add_argument("--learn", action="store_true")
    args = parser.parse_args()
    print(json.dumps(explain(args.url, args.learn), indent=2))
