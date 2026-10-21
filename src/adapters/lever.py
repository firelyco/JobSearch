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
    Includes the opening description, all named lists, closing text, and
    employer salary fields. Returns "" on failure.
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
    # Lever separates required qualifications from the opening description.
    # Returning only descriptionPlain silently omits the actual hiring gates.
    parts = [safe_str(data.get("descriptionPlain") or data.get("description"))]
    for section in data.get("lists") or []:
        if isinstance(section, dict):
            parts.extend([safe_str(section.get("text")), safe_str(section.get("content"))])
    parts.append(safe_str(data.get("additionalPlain") or data.get("additional")))
    salary = data.get("salaryRange")
    if isinstance(salary, dict):
        low, high = salary.get("min"), salary.get("max")
        currency, interval = salary.get("currency"), salary.get("interval")
        if all(value is not None for value in (low, high, currency, interval)):
            # Keep the employer's unit and currency; never infer annual base.
            parts.append(f"Published salary range: {currency} {low} to {high} {interval}")
    parts.append(safe_str(data.get("salaryDescriptionPlain") or data.get("salaryDescription")))
    return "\n".join(part for part in parts if part)
