"""Lever public postings API adapter.

Endpoint: GET https://api.lever.co/v0/postings/{tenant}?mode=json
No auth required. Returns a JSON array of postings directly.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

BASE = "https://api.lever.co/v0/postings"
TIMEOUT = 15


def fetch(tenant: str) -> list[Job]:
    url = f"{BASE}/{tenant}?mode=json"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("lever %s returned HTTP %d", tenant, r.status_code)
            return []
        data = r.json()
    except requests.RequestException as e:
        log.warning("lever %s request failed: %s", tenant, e)
        return []
    except ValueError as e:
        log.warning("lever %s returned invalid JSON: %s", tenant, e)
        return []

    raw_jobs = data if isinstance(data, list) else []
    results: list[Job] = []
    for j in raw_jobs:
        if not isinstance(j, dict):
            continue
        job_id = safe_str(j.get("id"))
        if not job_id:
            continue
        created_ms = j.get("createdAt")
        posted_at = ""
        if isinstance(created_ms, (int, float)) and created_ms > 0:
            try:
                posted_at = datetime.fromtimestamp(
                    created_ms / 1000, tz=timezone.utc
                ).isoformat()
            except (ValueError, OSError):
                posted_at = ""
        location = safe_str((j.get("categories") or {}).get("location"))
        results.append(Job(
            id=job_id,
            source="lever",
            company=tenant,
            title=safe_str(j.get("text")),
            location=location,
            url=safe_str(j.get("hostedUrl")),
            posted_at=posted_at,
            updated_at=posted_at,
        ))
    log.info("lever %s: %d jobs", tenant, len(results))
    return results


def fetch_detail(job: Job) -> str:
    """Fetch the full JD body for a single Lever posting.

    Endpoint: GET /v0/postings/{tenant}/{posting_id}?mode=json
    Returns the `descriptionPlain` field (plain text) when present,
    else falls back to `description` (HTML). Returns "" on failure.
    """
    tenant = job.get("company", "")
    posting_id = job.get("id", "")
    if not tenant or not posting_id:
        return ""
    url = f"{BASE}/{tenant}/{posting_id}?mode=json"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("lever detail %s/%s HTTP %d", tenant, posting_id, r.status_code)
            return ""
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("lever detail %s/%s failed: %s", tenant, posting_id, e)
        return ""
    if not isinstance(data, dict):
        return ""
    return safe_str(data.get("descriptionPlain") or data.get("description"))
