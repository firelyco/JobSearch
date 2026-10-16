"""Public Harmonic Hot 25 -> employer websites -> supported public ATS boards.

Harmonic rankings are company leads, never job postings or fit decisions.
No private Harmonic account, candidate profile, or search API is used.
"""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse
import requests
from src.discovery import board_from_url, board_key, cached_call, canonical_url

HOME = "https://harmonic.ai/"
TIMEOUT = 12

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.href=None; self.text=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=="a": self.href=attrs.get("href"); self.text=[]
        if tag in {"iframe","script"} and attrs.get("src") and board_from_url(attrs["src"]): self.links.append((attrs["src"], "Careers"))
    def handle_data(self, data):
        if self.href: self.text.append(data)
    def handle_endtag(self, tag):
        if tag=="a" and self.href:
            self.links.append((self.href," ".join(self.text))); self.href=None

def links(html, base):
    parser=Links(); parser.feed(html)
    return [(urljoin(base,href),text) for href,text in parser.links]

def public_url(url):
    if url.startswith("http://"): url="https://"+url[7:]
    normalized=canonical_url(url); host=urlparse(normalized).hostname or ""
    if not host or "." not in host or host.endswith((".local", ".internal")) or re.fullmatch(r"[\d.]+",host): return ""
    return normalized

def fetch_html(url):
    response=requests.get(url,timeout=TIMEOUT)
    response.raise_for_status()
    if response.status_code!=200 or not public_url(response.url): raise ValueError("Unsupported public page")
    return response.text,response.url

def latest_list(html):
    candidates=[]
    for url,_ in links(html,HOME):
        parsed=urlparse(url)
        match=re.fullmatch(r"/hot-25-startups/q([1-4])-(20\d{2})/?",parsed.path)
        if parsed.hostname in {"harmonic.ai","www.harmonic.ai"} and match:
            candidates.append(((int(match[2]),int(match[1])),url))
    if not candidates: raise ValueError("Harmonic public list links changed")
    return max(candidates)[1]

def company_leads(html, list_url):
    companies={}
    for url,text in links(html,list_url):
        url=public_url(url);host=urlparse(url).hostname or ""
        if not url or host in {"harmonic.ai","www.harmonic.ai"} or host.endswith(("linkedin.com","x.com","twitter.com")): continue
        if "\u2197" not in text: continue
        name=re.sub(r"^\s*(?:[1-9]|1\d|2[0-5])\s+","",text).replace("\u2197","").replace("\ufe0e","").strip()
        if name: companies.setdefault(host.removeprefix("www."),{"name":name,"url":url,"list_url":list_url})
    return list(companies.values())

def employer_boards(company, max_pages=3):
    queue=[company["url"]];visited=set();boards={};failed=False
    while queue and len(visited)<max_pages:
        url=queue.pop(0)
        if url in visited: continue
        visited.add(url)
        try: html,final=fetch_html(url)
        except (requests.RequestException,ValueError): failed=True;continue
        if board:=board_from_url(final):
            boards[board_key(board)]=board;break
        for href,text in links(html,final):
            href=public_url(href)
            if not href: continue
            board=board_from_url(href)
            if board: boards[board_key(board)]=board;continue
            host=urlparse(href).hostname or "";root=urlparse(final).hostname or ""
            same_employer=host.removeprefix("www.")==root.removeprefix("www.") or host.endswith("."+root.removeprefix("www."))
            if same_employer and re.search(r"careers?|\bjobs\b|join(?:[-/ ]us| the team)",href+" "+text,re.I) and href not in visited and href not in queue:
                queue.append(href)
        if boards: break
    return {"boards":list(boards.values()),"complete":bool(boards) and not failed}

def discover(config, cache):
    if not config.get("enabled",False): return [],[]
    def fetch_list():
        html,_=fetch_html(HOME);url=latest_list(html);html,_=fetch_html(url)
        companies=company_leads(html,url)
        if len(companies)<int(config.get("minimum_companies",10)): raise ValueError("Harmonic company list changed")
        return {"url":url,"companies":companies}
    try: listing,status=cached_call(cache,"harmonic:hot25",24,fetch_list)
    except (requests.RequestException,ValueError,KeyError,TypeError):
        return [],[{"source":"harmonic","label":"Harmonic public Hot 25","url":HOME,"status":"failed","leads":0}]
    companies=listing["companies"][:int(config.get("max_companies",25))]
    def resolve(company):
        key="harmonic:employer:"+company["url"]
        try:
            result,state=cached_call(cache,key,6,lambda:employer_boards(company))
            # Cached incomplete sites remain partial, never healthy empty results.
            return result["boards"],{"source":"harmonic_employer","label":"Harmonic: "+company["name"],"url":company["url"],"status":state if result["complete"] else "partial","leads":len(result["boards"])}
        except (requests.RequestException,ValueError,KeyError,TypeError):
            return [],{"source":"harmonic_employer","label":"Harmonic: "+company["name"],"url":company["url"],"status":"failed","leads":0}
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(resolve,companies))
    boards={board_key(b):b for found,_ in results for b in found}
    origins=cache.setdefault("harmonic:board_origins",{})
    for company,(found,_) in zip(companies,results):
        for board in found: origins[board_key(board)]={"company_url":company["url"],"list_url":listing["url"]}
    health=[{"source":"harmonic","label":"Harmonic public Hot 25","url":listing["url"],"status":status,"companies":len(companies),"leads":len(boards)}]
    health.extend(h for _,h in results)
    if len(companies)<len(listing["companies"]): health.append({"source":"harmonic","label":"Harmonic company limit","status":"limit_reached","deferred":len(listing["companies"])-len(companies)})
    return list(boards.values()),health


def tag_jobs(jobs, cache):
    """Preserve ATS job identity and attach public discovery provenance."""
    origins={}
    import json
    for key,origin in cache.get("harmonic:board_origins",{}).items():
        board=json.loads(key);cfg=board["config"]
        company=cfg.get("tenant") if isinstance(cfg,dict) else cfg
        origins[(board["source"],company)]=origin
    for job in jobs:
        origin=origins.get((job.get("source"),job.get("company")))
        if origin:
            job["discovery_sources"]=list(dict.fromkeys([*job.get("discovery_sources",[]),"harmonic"]))
            job["discovery_url"]=origin["list_url"]
