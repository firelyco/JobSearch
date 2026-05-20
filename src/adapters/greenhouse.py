"""Greenhouse Job Board API adapter.

Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs
No auth required for GET. Returns JSON with a `jobs` array.
"""
from __future__ import annotations
import logging
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

BASE = "https://boards-api.greenhouse.io/v1/boards"
TIMEOUT = 15


def fetch(tenant: str) -> list[Job]:
    """Fetch all current job postings for a Greenhouse tenant.

    Returns [] on any error (logged), never raises. The poller should be
    resilient — one bad tenant doesn't kill the whole run.
    """
    url = f"{BASE}/{tenant}/jobs"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("greenhouse %s returned HTTP %d", tenant, r.status_code)
            return []
        data = r.json()
    except requests.RequestException as e:
        log.warning("greenhouse %s request failed: %s", tenant, e)
        return []
    except ValueError as e:
        log.warning("greenhouse %s returned invalid JSON: %s", tenant, e)
        return []

    raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
    results: list[Job] = []
    for j in raw_jobs:
        if not isinstance(j, dict):
            continue
        job_id = safe_str(j.get("id"))
        if not job_id:
            continue
        location = safe_str((j.get("location") or {}).get("name"))
        results.append(Job(
            id=job_id,
            source="greenhouse",
            company=tenant,
            title=safe_str(j.get("title")),
            location=location,
            url=safe_str(j.get("absolute_url")),
            posted_at=safe_str(j.get("first_published")),
            updated_at=safe_str(j.get("updated_at")),
        ))
    log.info("greenhouse %s: %d jobs", tenant, len(results))
    return results
