"""Employer-independent discovery. Search results are leads, never job facts.

Public feeds work without credentials. Brave web search is optional and only
receives public role queries, never the candidate profile or private history.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
import requests

TIMEOUT = 20
ATS = {"greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io"),
       "lever": ("jobs.lever.co",), "ashby": ("jobs.ashbyhq.com",)}

def canonical_url(url):
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return ""
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k not in {"source", "ref", "referrer"}]
    return urlunparse(("https", p.netloc.lower(), p.path.rstrip("/"), "", urlencode(sorted(query)), ""))

def board_from_url(url):
    p = urlparse(canonical_url(url))
    parts = [s for s in p.path.split("/") if s]
    for source, hosts in ATS.items():
        if p.hostname in hosts and parts and re.fullmatch(r"[\w-]+", parts[0]):
            return {"source": source, "config": parts[0]}
    host = p.hostname or ""
    m = re.fullmatch(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if m and parts:
        site = parts[1] if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]) and len(parts) > 1 else parts[0]
        if re.fullmatch(r"[\w-]+", site):
            return {"source": "workday", "config": {"tenant": m[1], "site": site, "wd_server": m[2], "search": "program"}}
    return None

def board_key(board):
    return json.dumps(board, sort_keys=True)

class JobPage(HTMLParser):
    def __init__(self):
        super().__init__(); self.capture = False; self.parts = []; self.documents = []; self.links = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.capture = True; self.parts = []
        if tag == "a" and attrs.get("href", "").startswith("https://"):
            self.links.append(attrs["href"])
    def handle_data(self, data):
        if self.capture: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == "script" and self.capture:
            self.capture = False
            try: self.documents.append(json.loads("".join(self.parts)))
            except ValueError: pass

def job_documents(value):
    if isinstance(value, list):
        for item in value: yield from job_documents(item)
    elif isinstance(value, dict):
        kinds = value.get("@type", [])
        if kinds == "JobPosting" or isinstance(kinds, list) and "JobPosting" in kinds: yield value
        if "@graph" in value: yield from job_documents(value["@graph"])

def jobs_from_page(html, url, now=None):
    parser = JobPage(); parser.feed(html)
    now = now or datetime.now(timezone.utc)
    jobs = []
    for doc in job_documents(parser.documents):
        until = doc.get("validThrough")
        if until:
            try:
                stamp = datetime.fromisoformat(until.replace("Z", "+00:00"))
                if stamp.replace(tzinfo=stamp.tzinfo or timezone.utc) < now: continue
            except (ValueError, TypeError): pass
        company = doc.get("hiringOrganization", {}).get("name", "")
        title = doc.get("title", "")
        if not company or not title: continue
        locations = doc.get("jobLocation", [])
        if isinstance(locations, dict): locations = [locations]
        places = []
        for loc in locations:
            address = loc.get("address", {}) if isinstance(loc, dict) else {}
            if isinstance(address, dict): places.append(", ".join(str(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k)))
        jobs.append({"source": "web_discovery", "company": company, "title": title,
                     "id": hashlib.sha256(canonical_url(url).encode()).hexdigest()[:20],
                     "url": canonical_url(url), "location": " | ".join(places),
                     "posted_at": doc.get("datePosted", ""), "updated_at": "",
                     "description": doc.get("description", ""), "open_status": "structured_posting",
                     "checked_at": now.isoformat()})
    return jobs, [b for link in parser.links if (b := board_from_url(link))]

def cached_call(cache, key, hours, fn):
    entry = cache.get(key, {})
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(entry["checked_at"])
        if age < timedelta(hours=hours): return entry["data"], "cached"
    except (KeyError, ValueError, TypeError): pass
    data = fn()
    cache[key] = {"checked_at": datetime.now(timezone.utc).isoformat(), "data": data}
    return data, "ok"

def get_json(url, **kwargs):
    response = requests.get(url, timeout=TIMEOUT, **kwargs)
    response.raise_for_status()
    return response.json()

def remotive_jobs():
    data = get_json("https://remotive.com/api/remote-jobs")
    if not isinstance(data.get("jobs"), list): raise ValueError("Remotive schema changed")
    return [{"source": "remotive", "company": j["company_name"], "id": str(j["id"]),
             "title": j["title"], "location": j.get("candidate_required_location", ""),
             "url": j["url"], "posted_at": j.get("publication_date", ""), "updated_at": "",
             "description": j.get("description", ""), "open_status": "listed"} for j in data["jobs"]]

def arbeitnow_jobs():
    jobs = []
    for page in range(1, 4):
        data = get_json("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
        if not isinstance(data.get("data"), list): raise ValueError("Arbeitnow schema changed")
        for j in data["data"]:
            jobs.append({"source": "arbeitnow", "company": j["company_name"], "id": j["slug"],
                         "title": j["title"], "location": j.get("location", ""), "url": j["url"],
                         "posted_at": datetime.fromtimestamp(j["created_at"], timezone.utc).isoformat(),
                         "updated_at": "", "description": j.get("description", ""), "open_status": "listed"})
        if not data.get("links", {}).get("next"): break
    return jobs

def discover(config, cache):
    jobs, boards, health = [], [], []
    if not config.get("enabled", False): return jobs, boards, health
    for name, fn in (("remotive", remotive_jobs), ("arbeitnow", arbeitnow_jobs)):
        if name not in config.get("public_feeds", []): continue
        try:
            found, status = cached_call(cache, name, 6, fn)
            jobs.extend(found); health.append({"source": name, "status": status, "jobs": len(found)})
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            health.append({"source": name, "status": "failed", "jobs": 0})
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    urls = list(config.get("seed_urls", []))
    if not key:
        health.append({"source": "web_search", "status": "not_configured", "jobs": 0})
    else:
        for query in config.get("queries", [])[:12]:
            try:
                data, status = cached_call(cache, "search:" + query, 6, lambda: get_json(
                    "https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 20},
                    headers={"X-Subscription-Token": key, "Accept": "application/json"}))
                results = data.get("web", {}).get("results", [])
                urls.extend(r["url"] for r in results if "url" in r)
                health.append({"source": "web_search", "query": query, "status": status, "leads": len(results)})
            except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
                health.append({"source": "web_search", "query": query, "status": "failed"})
    for url in dict.fromkeys(urls):
        if board := board_from_url(url):
            boards.append(board); continue
        # Only employer career pages; aggregators/search snippets aren't verified jobs.
        host = urlparse(url).hostname or ""
        if not canonical_url(url) or not re.match(r"^(careers|jobs)\.[a-z0-9-]+\.[a-z.]+$", host): continue
        try:
            def fetch_page():
                response = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
                response.raise_for_status()
                if response.status_code != 200: raise ValueError("redirect requires inspection")
                found, learned = jobs_from_page(response.text, url)
                return {"jobs": found, "boards": learned}
            page, status = cached_call(cache, "page:" + url, 6, fetch_page)
            jobs.extend(page["jobs"]); boards.extend(page["boards"])
            health.append({"source": "career_page", "url": url, "status": status, "jobs": len(page["jobs"])})
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            health.append({"source": "career_page", "url": url, "status": "failed"})
    return jobs, list({board_key(b): b for b in boards}.values()), health

def merge_jobs(existing, discovered):
    """Existing keys win: application state and cached fit verdicts stay valid."""
    result = list(existing)
    keys = {f"{j['source']}:{j['company']}:{j['id']}" for j in existing}
    urls = {canonical_url(j.get("url", "")) for j in existing} - {""}
    for job in discovered:
        key = f"{job['source']}:{job['company']}:{job['id']}"
        url = canonical_url(job.get("url", ""))
        if key in keys or url and url in urls: continue
        result.append(job); keys.add(key)
        if url: urls.add(url)
    return result
