"""Ashby public job board API adapter.

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/{tenant}
No auth required. Returns {"apiVersion": "...", "jobs": [...]}.
"""
from __future__ import annotations
import logging
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

BASE = "https://api.ashbyhq.com/posting-api/job-board"
TIMEOUT = 15


def fetch(tenant: str) -> list[Job]:
    url = f"{BASE}/{tenant}"
    try:
        r = requests.get(url, timeout=TIMEOUT, params={"includeCompensation": "true"})
        if r.status_code != 200:
            log.warning("ashby %s returned HTTP %d", tenant, r.status_code)
            return []
        data = r.json()
    except requests.RequestException as e:
        log.warning("ashby %s request failed: %s", tenant, e)
        return []
    except ValueError as e:
        log.warning("ashby %s returned invalid JSON: %s", tenant, e)
        return []

    raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
    results: list[Job] = []
    for j in raw_jobs:
        if not isinstance(j, dict):
            continue
        job_id = safe_str(j.get("id"))
        if not job_id:
            continue
        results.append(Job(
            id=job_id,
            source="ashby",
            company=tenant,
            title=safe_str(j.get("title")),
            location=safe_str(j.get("locationName")),
            url=safe_str(j.get("jobUrl")),
            posted_at=safe_str(j.get("publishedAt")),
            updated_at=safe_str(j.get("updatedAt") or j.get("publishedAt")),
        ))
    log.info("ashby %s: %d jobs", tenant, len(results))
    return results
