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


def fetch_detail(job: Job) -> str:
    """Fetch the full JD body for a single Ashby job.

    Ashby's job board endpoint returns descriptionHtml inline in the
    listing — we just re-fetch the board and pick out the matching id.
    This avoids needing a separate per-job endpoint. Returns "" on failure.
    """
    tenant = job.get("company", "")
    job_id = job.get("id", "")
    if not tenant or not job_id:
        return ""
    url = f"{BASE}/{tenant}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("ashby detail %s HTTP %d", tenant, r.status_code)
            return ""
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("ashby detail %s failed: %s", tenant, e)
        return ""
    raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
    for j in raw_jobs:
        if isinstance(j, dict) and safe_str(j.get("id")) == job_id:
            return safe_str(j.get("descriptionHtml") or j.get("descriptionPlain"))
    return ""
