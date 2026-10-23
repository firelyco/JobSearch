"""Workday Candidate Experience Service (CXS) API adapter.

Endpoint: POST https://{tenant}.{wd_server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
Body: JSON {"appliedFacets": {}, "limit": N, "offset": N, "searchText": ""}
No auth required for public job boards. Returns {"jobPostings": [...], "total": N}.

Workday is the most complex adapter because every company hosts on its own
tenant URL with different data centers (wd1, wd3, wd5...). Each entry in
companies.yml under `workday:` is a dict with tenant + site + wd_server.

Pagination: response has `total`; we fetch up to PAGE_CAP pages of LIMIT each.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
import re
from urllib.parse import urlparse
import requests
from . import Job, JobBatch, safe_str

log = logging.getLogger(__name__)

LIMIT = 20
PAGE_CAP = 200
TIMEOUT = 20

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "en-US",
    "User-Agent": "Mozilla/5.0 (compatible; JobSearchBot/1.0)",
}

_REL_RE = re.compile(r"posted\s+(\d+)\s*\+?\s*(day|week|month|hour|minute)s?\s*ago", re.IGNORECASE)


def _parse_relative_posted(s: str) -> str:
    """Workday returns 'postedOn' as relative strings ('Posted Yesterday',
    'Posted 30+ Days Ago'). Convert to a best-effort ISO timestamp.
    """
    if not s:
        return ""
    low = s.lower().strip()
    # Day precision: a full-resolution now() would rewrite every Workday
    # posted_at on every poll, forcing a commit every 15 minutes.
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in low:
        return now.isoformat()
    if "yesterday" in low:
        return (now - timedelta(days=1)).isoformat()
    m = _REL_RE.search(low)
    if not m:
        return ""
    n = int(m.group(1))
    unit = m.group(2)
    delta = {
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=30 * n),
    }.get(unit, timedelta())
    return (now - delta).isoformat()


def fetch(config: dict) -> list[Job]:
    """Fetch all jobs for a Workday tenant.

    `config` is a dict with keys: tenant, site, wd_server, and an optional
    `search`. For large employers (e.g. Walmart, ~2000 postings) the first
    PAGE_CAP*LIMIT jobs are mostly irrelevant retail/ops roles, so the few
    TPM roles get missed. Set `search` (e.g. "program manager") to send it as
    Workday's searchText — the API then returns relevance-ranked matches and
    our scorer filters those to the target seniority. Omit it to fetch all.
    """
    tenant = config.get("tenant")
    site = config.get("site")
    wd_server = config.get("wd_server", "wd3")
    search = config.get("search", "") or ""
    if not tenant or not site:
        log.warning("workday config missing tenant/site: %s", config)
        return []

    base_host = f"https://{tenant}.{wd_server}.myworkdayjobs.com"
    api_url = f"{base_host}/wday/cxs/{tenant}/{site}/jobs"
    headers = dict(HEADERS)
    headers["Referer"] = f"{base_host}/en-US/{site}"

    results: list[Job] = []
    offset = 0
    total = None
    seen = set()
    complete = False
    pages = 0
    for _ in range(int(config.get("max_pages", PAGE_CAP))):
        body = {"appliedFacets": config.get("applied_facets", {}), "limit": LIMIT, "offset": offset, "searchText": search}
        try:
            r = requests.post(api_url, json=body, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            log.warning("workday %s request failed: %s", tenant, e)
            break
        if r.status_code in (401, 403, 422):
            log.info("workday %s: public API disabled (HTTP %d)", tenant, r.status_code)
            break
        if r.status_code != 200:
            log.warning("workday %s returned HTTP %d", tenant, r.status_code)
            break
        try:
            data = r.json()
        except ValueError:
            log.warning("workday %s returned invalid JSON", tenant)
            break

        if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
            log.warning("workday %s: listing schema changed", tenant)
            break
        pages += 1
        postings = data["jobPostings"]
        before = len(results)
        # Many tenants report `total` only on the first page and 0 after it;
        # re-reading it every page stopped pagination at 40 jobs.
        page_total = int(data.get("total", 0) or 0) if isinstance(data, dict) else 0
        if offset == 0 or page_total:
            total = page_total

        for j in postings:
            if not isinstance(j, dict):
                continue
            external_path = safe_str(j.get("externalPath"))
            job_id = external_path or safe_str(j.get("bulletFields", [""])[0] if j.get("bulletFields") else "")
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            full_url = f"{base_host}/en-US/{site}{external_path}" if external_path else ""
            results.append(Job(
                id=job_id,
                source="workday",
                company=tenant,
                title=safe_str(j.get("title")),
                location=safe_str(j.get("locationsText")),
                url=full_url,
                posted_at=_parse_relative_posted(safe_str(j.get("postedOn"))),
                updated_at="",
            ))

        offset += len(postings)
        if total is not None and len(results) >= total:
            complete = True
            break
        if not postings or len(results) == before:
            log.warning("workday %s: pagination stopped before reported total", tenant)
            break

    if total is not None and not complete:
        log.warning("workday %s: incomplete enumeration, collected %d of %d", tenant, len(results), total)
    log.info("workday %s: %d jobs", tenant, len(results))
    return JobBatch(results, reported_total=total, collected=len(results), pages=pages,
                    enumeration_complete=complete, query=search)


def fetch_info(job: Job) -> dict:
    """Fetch public metadata and the full HTML JD for a single Workday job.

    Requires a second API call to:
      GET {base_host}/wday/cxs/{tenant}/{site}{externalPath}
    where externalPath is what we stored as the job id at poll time and
    already starts with '/job/'. The response shape is
    {jobPostingInfo: {jobDescription: "<html>...</html>", ...}}.

    Returns an empty value on failure. The host (which carries the wd1/wd5/wd12 data
    center) and the site aren't in the normalized Job dict, so both are
    recovered from the stored public url.
    """
    tenant = job.get("company", "")
    external_path = job.get("id", "")
    if not tenant or not external_path:
        return ""
    # url looks like https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/...
    url = job.get("url", "")
    site = _extract_site_from_url(url)
    host = urlparse(url).hostname or ""
    if not site or not host.endswith(".myworkdayjobs.com"):
        log.warning("workday detail %s: cannot extract host/site from url=%r", tenant, url)
        return ""
    base_host = f"https://{host}"
    api_url = f"{base_host}/wday/cxs/{tenant}/{site}{external_path}"
    headers = dict(HEADERS)
    headers["Referer"] = f"{base_host}/en-US/{site}"
    try:
        r = requests.get(api_url, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("workday detail %s HTTP %d", tenant, r.status_code)
            return ""
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("workday detail %s failed: %s", tenant, e)
        return ""
    info = data.get("jobPostingInfo", {}) if isinstance(data, dict) else {}
    return info if isinstance(info, dict) else {}


def fetch_detail(job: Job) -> str:
    return safe_str((fetch_info(job) or {}).get("jobDescription"))


def location_from_info(info):
    places = []
    for value in [info.get("location", ""), *info.get("additionalLocations", [])]:
        if isinstance(value, dict):
            value = value.get("descriptor") or value.get("location") or ""
        if value:
            places.append(str(value))
    return " | ".join(dict.fromkeys(places))


def _extract_site_from_url(url: str) -> str:
    """url is like https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/..."""
    if not url:
        return ""
    marker = ".myworkdayjobs.com/en-US/"
    idx = url.find(marker)
    if idx < 0:
        return ""
    rest = url[idx + len(marker):]
    return rest.split("/", 1)[0]
