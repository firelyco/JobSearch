"""Apple careers adapter (jobs.apple.com).

Apple's JSON API requires a session, but the public search and detail pages
embed their data as router hydration JSON:

  <script>window.__staticRouterHydrationData = JSON.parse("...");</script>

Search: GET https://jobs.apple.com/en-us/search?search=...&location=united-states-USA&page=N
  -> loaderData.search = {searchResults: [...20 per page], totalRecords}
  Each result has positionId, postingTitle, transformedPostingTitle,
  postDateInGMT and locations[].name.
Detail: GET https://jobs.apple.com/en-us/details/{positionId}/{slug}
  -> loaderData.jobDetails.jobsData with jobSummary, description,
  minimumQualifications, preferredQualifications.

Apple's search is fuzzy (an unquoted query matches thousands of roles), so
queries in companies.yml should be quoted phrases:
  apple:
    - '"engineering program manager"'

This parses page markup rather than a documented API, so a site redesign
can break it; failures log and return [] like every other adapter.
"""
from __future__ import annotations
import json
import logging
import re
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

BASE = "https://jobs.apple.com/en-us"
LOCATION = "united-states-USA"
PAGE_SIZE = 20          # fixed by the site
PAGE_CAP = 10           # up to 200 results per query
TIMEOUT = 20

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobSearchBot/1.0)"}

_HYDRATION_RE = re.compile(
    r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((".*?")\);', re.S
)


def _hydration(html: str) -> dict:
    """Extract the router hydration payload; {} if absent or malformed."""
    m = _HYDRATION_RE.search(html or "")
    if not m:
        return {}
    try:
        # The payload is a JSON string literal whose content is itself JSON.
        data = json.loads(json.loads(m.group(1)))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def fetch(query: str) -> list[Job]:
    """Fetch Apple US postings matching a search query. Returns [] on any error."""
    if not query:
        return []
    results: list[Job] = []
    for page in range(1, PAGE_CAP + 1):
        params = {"search": query, "location": LOCATION, "page": page}
        try:
            r = requests.get(f"{BASE}/search", params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning("apple %r returned HTTP %d", query, r.status_code)
                break
        except requests.RequestException as e:
            log.warning("apple %r failed: %s", query, e)
            break
        search = (_hydration(r.text).get("loaderData") or {}).get("search") or {}
        rows = search.get("searchResults") or []
        if not rows:
            if page == 1:
                log.warning("apple %r: no search data in page (layout change?)", query)
            break
        for x in rows:
            if not isinstance(x, dict):
                continue
            pos_id = safe_str(x.get("positionId"))
            if not pos_id:
                continue
            slug = safe_str(x.get("transformedPostingTitle"))
            locs = [safe_str(l.get("name")) for l in x.get("locations") or [] if isinstance(l, dict)]
            results.append(Job(
                id=pos_id,
                source="apple",
                company="apple",
                title=safe_str(x.get("postingTitle")),
                location="; ".join(l for l in locs if l) + ", United States" if locs else "United States",
                url=f"{BASE}/details/{pos_id}/{slug}" if slug else f"{BASE}/details/{pos_id}",
                posted_at=safe_str(x.get("postDateInGMT")),
                updated_at="",
            ))
        total = int(search.get("totalRecords") or 0)
        if page * PAGE_SIZE >= total:
            break

    log.info("apple %r: %d jobs", query, len(results))
    return results


def fetch_detail(job: Job) -> str:
    """Fetch the JD for one Apple posting as concatenated HTML-ish text."""
    url = job.get("url", "")
    if not url:
        return ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("apple detail %s HTTP %d", job.get("id"), r.status_code)
            return ""
    except requests.RequestException as e:
        log.warning("apple detail %s failed: %s", job.get("id"), e)
        return ""
    details = (_hydration(r.text).get("loaderData") or {}).get("jobDetails") or {}
    data = details.get("jobsData") or {}
    parts = []
    for label, key in (("Summary", "jobSummary"), ("Description", "description"),
                       ("Minimum Qualifications", "minimumQualifications"),
                       ("Preferred Qualifications", "preferredQualifications")):
        value = safe_str(data.get(key))
        if value:
            parts.append(f"{label}\n{value}")
    return "\n\n".join(parts)
