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


def _place(name: str, address) -> str:
    """'San Francisco' + postal address -> 'San Francisco, California, United States'."""
    postal = (address or {}).get("postalAddress") or {} if isinstance(address, dict) else {}
    parts = [safe_str(name)]
    for key in ("addressRegion", "addressCountry"):
        v = safe_str(postal.get(key))
        if v and v.lower() not in " ".join(parts).lower():
            parts.append(v)
    return ", ".join(p for p in parts if p)


def _location(j: dict) -> str:
    """Build a location string from Ashby's posting fields.

    The API now sends `location` (city name) plus a structured `address` and
    `secondaryLocations`; the old `locationName` field is gone, which left
    every Ashby job's location blank (no US bonus, failed the US filter).
    """
    places = [_place(j.get("location") or j.get("locationName"), j.get("address"))]
    for sec in j.get("secondaryLocations") or []:
        if isinstance(sec, dict):
            places.append(_place(sec.get("location"), sec.get("address")))
    loc = "; ".join(dict.fromkeys(p for p in places if p))
    if j.get("isRemote") is True and "remote" not in loc.lower():
        loc = f"Remote; {loc}" if loc else "Remote"
    return loc


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
            location=_location(j),
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
        r = requests.get(url, timeout=TIMEOUT, params={"includeCompensation":"true"})
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
            from src.compensation import ashby_salary
            text=safe_str(j.get("descriptionHtml") or j.get("descriptionPlain"))
            pay=ashby_salary(j)
            return text+("\n"+pay if pay else "")
    return ""
