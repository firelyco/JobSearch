"""Oracle Cloud Recruiting (HCM Candidate Experience) adapter.

Used by employers who moved from Workday to Oracle's Recruiting Cloud —
including Oracle itself and Dell. Both expose the same public CE REST API:

  GET https://{tenant}.fa.{dc}.oraclecloud.com/hcmRestApi/resources/latest/
      recruitingCEJobRequisitions
      ?onlyData=true
      &expand=requisitionList
      &finder=findReqs;siteNumber={SITE_NO},keyword={q},limit={L},offset={N}

Response: {items: [{TotalJobsCount, requisitionList: [...]}]}. Each requisition
has Id, Title, PrimaryLocation, PostedDate (already ISO), ShortDescriptionStr.

Full JD (for tailoring / fit) is on the sibling resource:
  GET .../hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails/{Id}
which returns ExternalDescriptionStr + ExternalResponsibilitiesStr +
ExternalQualificationsStr + ShortDescriptionStr.

Config per entry (companies.yml under `oracle_cloud:`):
  - company:     friendly name (oracle / dell)
    tenant:      Oracle Cloud tenant prefix (eeho / iawmqy)
    dc:          datacenter slug (us2 / ocs)
    site:        site path in the CE URL (jobsearch / careers)
    site_number: siteNumber finder param (CX_45001 / CX_1001)
    search:      keyword query (e.g. "program manager")
"""
from __future__ import annotations
import logging
from urllib.parse import urlparse
import requests
from . import Job, safe_str

log = logging.getLogger(__name__)

LIMIT = 50
PAGE_CAP = 6           # up to 300 results per (company, search)
TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobSearchBot/1.0)",
    "Accept": "application/json",
}


def _api_root(host: str) -> str:
    return f"https://{host}/hcmRestApi/resources/latest"


def _ce_root(host: str) -> str:
    return f"https://{host}/hcmUI/CandidateExperience/en"


def fetch(config: dict) -> list[Job]:
    """Fetch jobs for one Oracle Cloud tenant. Returns [] on any error."""
    tenant = config.get("tenant")
    dc = config.get("dc")
    site = safe_str(config.get("site"))
    site_number = safe_str(config.get("site_number"))
    search = safe_str(config.get("search"))
    company = safe_str(config.get("company")) or tenant or ""
    if not tenant or not dc or not site_number:
        log.warning("oracle_cloud config missing tenant/dc/site_number: %s", config)
        return []

    host = f"{tenant}.fa.{dc}.oraclecloud.com"
    list_url = f"{_api_root(host)}/recruitingCEJobRequisitions"
    ce = _ce_root(host)

    results: list[Job] = []
    offset = 0
    for _ in range(PAGE_CAP):
        finder = f"findReqs;siteNumber={site_number}"
        if search:
            finder += f",keyword={search}"
        finder += f",limit={LIMIT},offset={offset}"
        try:
            r = requests.get(
                list_url,
                params={"onlyData": "true", "expand": "requisitionList", "finder": finder},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                log.warning("oracle_cloud %s HTTP %d", company, r.status_code)
                break
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("oracle_cloud %s failed: %s", company, e)
            break

        items = data.get("items") or []
        if not items or not isinstance(items[0], dict):
            break
        sc = items[0]
        req_list = sc.get("requisitionList") or []
        total = int(sc.get("TotalJobsCount") or 0)
        if not req_list:
            break

        for j in req_list:
            if not isinstance(j, dict):
                continue
            req_id = safe_str(j.get("Id"))
            if not req_id:
                continue
            site_segment = site or "jobsearch"
            results.append(Job(
                id=req_id,
                source="oracle_cloud",
                company=company,
                title=safe_str(j.get("Title")),
                location=safe_str(j.get("PrimaryLocation")),
                url=f"{ce}/sites/{site_segment}/job/{req_id}",
                posted_at=safe_str(j.get("PostedDate")),
                updated_at="",
            ))

        offset += LIMIT
        if total and offset >= total:
            break

    log.info("oracle_cloud %s: %d jobs", company, len(results))
    return results


def fetch_detail(job: Job) -> str:
    """Fetch the full JD for one job. Returns concatenated description fields
    (each is already HTML-ish; jd_fetch.strip_html cleans them). "" on failure.
    """
    url = job.get("url", "")
    req_id = job.get("id", "")
    if not url or not req_id:
        return ""
    host = (urlparse(url).hostname or "")
    if not host:
        return ""
    api = f"{_api_root(host)}/recruitingCEJobRequisitionDetails/{req_id}"
    try:
        r = requests.get(api, params={"onlyData": "true"}, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("oracle_cloud detail %s HTTP %d", req_id, r.status_code)
            return ""
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("oracle_cloud detail %s failed: %s", req_id, e)
        return ""

    parts: list[str] = []
    for k in ("ShortDescriptionStr", "ExternalDescriptionStr",
              "ExternalResponsibilitiesStr", "ExternalQualificationsStr"):
        v = d.get(k)
        if v:
            parts.append(str(v))
    return "\n\n".join(parts)
