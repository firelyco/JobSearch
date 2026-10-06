"""Netflix careers adapter (Eightfold-hosted explore.jobs.netflix.net).

Netflix left Lever; its board now runs on Eightfold, which exposes a public
JSON search API:

  GET https://explore.jobs.netflix.net/api/apply/v2/jobs
      ?domain=netflix.com&query=...&num=10&start=N

returning {"count": int, "positions": [...]}. Each position has id, name,
location, t_create (epoch seconds) and canonicalPositionUrl. The server caps
`num` at 10 per page. The detail endpoint .../jobs/{id}?domain=netflix.com
adds job_description (HTML).

Config (companies.yml) is a list of search query strings:
  netflix:
    - "program manager"
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

BASE = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
DOMAIN = "netflix.com"
PAGE_SIZE = 10          # server maximum
PAGE_CAP = 25           # up to 250 results per query
TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobSearchBot/1.0)",
    "Accept": "application/json",
}


def _epoch_to_iso(value) -> str:
    try:
        secs = int(value)
    except (TypeError, ValueError):
        return ""
    if secs <= 0:
        return ""
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


def fetch(query: str) -> list[Job]:
    """Fetch Netflix postings matching a search query. Returns [] on any error."""
    if not query:
        return []
    results: list[Job] = []
    start = 0
    for _ in range(PAGE_CAP):
        params = {"domain": DOMAIN, "query": query, "num": PAGE_SIZE, "start": start}
        try:
            r = requests.get(BASE, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning("netflix %r returned HTTP %d", query, r.status_code)
                break
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("netflix %r failed: %s", query, e)
            break

        positions = data.get("positions", []) if isinstance(data, dict) else []
        count = int(data.get("count", 0) or 0) if isinstance(data, dict) else 0
        if not positions:
            break
        for p in positions:
            if not isinstance(p, dict):
                continue
            job_id = safe_str(p.get("id"))
            if not job_id:
                continue
            created = _epoch_to_iso(p.get("t_create"))
            results.append(Job(
                id=job_id,
                source="netflix",
                company="netflix",
                title=safe_str(p.get("name") or p.get("posting_name")),
                location=safe_str(p.get("location")),
                url=safe_str(p.get("canonicalPositionUrl")),
                posted_at=created,
                updated_at=_epoch_to_iso(p.get("t_update")) or created,
            ))
        start += PAGE_SIZE
        if start >= count:
            break

    log.info("netflix %r: %d jobs", query, len(results))
    return results


def fetch_detail(job: Job) -> str:
    """Fetch the full JD HTML for one Netflix posting. Returns "" on failure."""
    job_id = job.get("id", "")
    if not job_id:
        return ""
    try:
        r = requests.get(f"{BASE}/{job_id}", params={"domain": DOMAIN}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("netflix detail %s HTTP %d", job_id, r.status_code)
            return ""
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("netflix detail %s failed: %s", job_id, e)
        return ""
    return safe_str(data.get("job_description")) if isinstance(data, dict) else ""
