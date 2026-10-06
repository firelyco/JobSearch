"""Google careers adapter (google.com/about/careers).

Google has no public jobs API, but the results page embeds its data in an
AF_initDataCallback block (key 'ds:1'):

  data = [[job, job, ...], None, total, page_size]

Each job is a positional array. Fields used here:
  0  id                      9   locations [[label, ..., city, ?, state, country], ...]
  1  title                   10  about-the-job HTML
  3  [_, responsibilities]   12  [created_epoch_secs, nanos]
  4  [_, qualifications]     20  level: 1 early, 2 mid, 3 advanced, 4 director+, 5 intern
  7  company label (Google, DeepMind, YouTube, ...)

The search takes target_level filters, so config entries ask only for
ADVANCED and DIRECTOR_PLUS roles; titles alone ("Technical Program Manager,
Data Center Networking") don't show level at Google.

Config (companies.yml):
  google:
    - query: '"program manager"'
      levels: [ADVANCED, DIRECTOR_PLUS]

Positional parsing of an internal format: a site change can break it, and
failures log and return [] like every other adapter.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

RESULTS = "https://www.google.com/about/careers/applications/jobs/results"
LOCATION = "United States"
PAGE_SIZE = 20
PAGE_CAP = 10           # up to 200 results per query
TIMEOUT = 20

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobSearchBot/1.0)"}

_CALLBACK_RE = re.compile(
    r"AF_initDataCallback\(\{key: '(ds:\d+)'.*?data:(.*?), sideChannel: \{\}\}\);", re.S
)


def _callbacks(html: str) -> dict[str, object]:
    """Map callback key -> decoded data for every AF_initDataCallback block."""
    out: dict[str, object] = {}
    for m in _CALLBACK_RE.finditer(html or ""):
        try:
            out[m.group(1)] = json.loads(m.group(2))
        except ValueError:
            continue
    return out


def _at(row: list, i: int):
    return row[i] if isinstance(row, list) and len(row) > i else None


def _html_field(row: list, i: int) -> str:
    v = _at(row, i)
    return safe_str(v[1]) if isinstance(v, list) and len(v) > 1 else ""


def _to_job(row: list) -> Job | None:
    job_id = safe_str(_at(row, 0))
    title = safe_str(_at(row, 1))
    if not job_id or not title:
        return None
    locs = [safe_str(l[0]) for l in _at(row, 9) or [] if isinstance(l, list) and l]
    created = _at(row, 12)
    posted = ""
    if isinstance(created, list) and created and isinstance(created[0], (int, float)) and created[0] > 0:
        posted = datetime.fromtimestamp(created[0], tz=timezone.utc).isoformat()
    return Job(
        id=job_id,
        source="google",
        company="google",
        title=title,
        location="; ".join(l for l in locs if l),
        url=f"{RESULTS}/{job_id}",
        posted_at=posted,
        updated_at="",
    )


def fetch(config: dict | str) -> list[Job]:
    """Fetch Google US roles for one query. Returns [] on any error."""
    if isinstance(config, str):
        config = {"query": config}
    query = safe_str((config or {}).get("query"))
    levels = (config or {}).get("levels") or []
    if not query:
        return []
    results: list[Job] = []
    for page in range(1, PAGE_CAP + 1):
        params = [("q", query), ("location", LOCATION), ("page", str(page))]
        params += [("target_level", lv) for lv in levels]
        try:
            r = requests.get(RESULTS, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning("google %r returned HTTP %d", query, r.status_code)
                break
        except requests.RequestException as e:
            log.warning("google %r failed: %s", query, e)
            break
        data = _callbacks(r.text).get("ds:1")
        rows = _at(data, 0) if isinstance(data, list) else None
        if not rows:
            if page == 1:
                log.warning("google %r: no results data in page (layout change?)", query)
            break
        for row in rows:
            job = _to_job(row)
            if job:
                results.append(job)
        total = _at(data, 2)
        if not isinstance(total, int) or page * PAGE_SIZE >= total:
            break

    log.info("google %r: %d jobs", query, len(results))
    return results


def fetch_detail(job: Job) -> str:
    """Fetch the JD (about, responsibilities, qualifications) for one job."""
    job_id = job.get("id", "")
    if not job_id:
        return ""
    try:
        r = requests.get(f"{RESULTS}/{job_id}", headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("google detail %s HTTP %d", job_id, r.status_code)
            return ""
    except requests.RequestException as e:
        log.warning("google detail %s failed: %s", job_id, e)
        return ""
    # The detail page carries the same positional job array in one of its
    # callbacks; find the row whose id matches.
    for data in _callbacks(r.text).values():
        row = _find_row(data, job_id)
        if row:
            parts = [_html_field(row, i) for i in (10, 3, 4)]
            return "\n\n".join(p for p in parts if p)
    return ""


def _find_row(data, job_id: str, depth: int = 0):
    if depth > 6 or not isinstance(data, list):
        return None
    if len(data) > 1 and data[0] == job_id and isinstance(data[1], str):
        return data
    for item in data:
        found = _find_row(item, job_id, depth + 1)
        if found:
            return found
    return None
