"""Public investor/accelerator directories discover employers, never infer jobs.

Crawl a bounded rotating slice; poll learned ATS boards in the normal pipeline.
Blocked/dynamic job boards use public company directories and retain partial health.
"""
import json,re
import requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse,parse_qs,urlencode,urlunparse
from src.discovery import board_from_url,board_key,cached_call
from src.harmonic_discovery import fetch_html,links,public_url,employer_boards

SOURCES={
 'a16z':{'label':'a16z','url':'https://jobs.a16z.com/jobs','param':'titlePrefix'},
 'greylock':{'label':'Greylock','url':'https://greylock.com/jobs/portfolio-jobs/','param':'search'},
 'sequoia':{'label':'Sequoia','url':'https://jobs.sequoiacap.com/jobs','directory':'https://sequoiacap.com/companies/'},
 'yc':{'label':'Y Combinator','url':'https://www.ycombinator.com/jobs/role/operations','extra_pages':['https://www.ycombinator.com/jobs']},
 'techstars':{'label':'Techstars','url':'https://jobs.techstars.com/jobs','directory':'https://www.techstars.com/portfolio'},
}
TERMS=['technical program','engineering program','program management']
SOCIAL={'linkedin.com','twitter.com','x.com','facebook.com','youtube.com','instagram.com','startupschool.org','getro.com','consider.com'}

def external(url,base):
 host=(urlparse(url).hostname or '').removeprefix('www.');root=(urlparse(base).hostname or '').removeprefix('www.')
 return bool(host and host!=root and not host.endswith('.'+root) and not root.endswith('.'+host) and not any(host==s or host.endswith('.'+s) for s in SOCIAL))

def company_profiles(html,url,source):
 result={}
 for href,text in links(html,url):
  href=public_url(href);p=urlparse(href)
  if not href:continue
  if source in {'sequoia','yc'} and not external(href,url) and re.fullmatch(r'/companies/[^/]+/?',p.path):result[href]=href
  elif source=='techstars' and external(href,url) and p.path in {'','/'}:result[href]=href
 return list(result)

def profile_website(html,url):
 for href,text in links(html,url):
  href=public_url(href)
  if not href or not external(href,url):continue
  host=(urlparse(href).hostname or '').removeprefix('www.')
  if text.strip().lower() in {'website','visit website','company website'} or host in text.lower():return href
 return None

def page_url(url,page):
 p=urlparse(url);query=parse_qs(p.query)
 query['page']=[str(page)]
 return urlunparse(p._replace(query=urlencode(query,doseq=True)))

def sequoia_pages(settings,cache):
    # Same unauthenticated request used by the public Sequoia job-board UI.
    # Results are only employer-board leads; ATS responses establish job facts.
    boards={};candidates=[];complete=True;failures=0;client=None;csrf=None
    for term in TERMS:
        cursor_key='portfolio:sequoia_sequence:'+term
        sequence=cache.get(cursor_key)
        for _ in range(int(settings.get('max_pages_per_query',2))):
            payload={'meta':{'size':15},'board':{'id':'sequoia-capital','isParent':True},'query':{'titlePrefix':term,'promoteFeatured':True},'grouped':True}
            if sequence:payload['meta']['sequence']=sequence
            def fetch():
                nonlocal client,csrf
                if client is None:
                    client=requests.Session()
                    page=client.get('https://jobs.sequoiacap.com/jobs',timeout=15);page.raise_for_status()
                    match=re.search(r'"csrfToken"\s*:\s*"([^"]+)"',page.text)
                    if not match:raise ValueError('Public Sequoia session token missing')
                    csrf=match[1]
                response=client.post('https://jobs.sequoiacap.com/api-boards/search-jobs',json=payload,headers={'x-csrf-token':csrf,'Referer':'https://jobs.sequoiacap.com/jobs'},timeout=15)
                response.raise_for_status();data=response.json()
                if not isinstance(data.get('jobs'),list) or data.get('errors'):raise ValueError('Sequoia listing schema changed')
                return data
            try:data,_=cached_call(cache,'portfolio:sequoia_api:'+json.dumps(payload,sort_keys=True),6,fetch)
            except (requests.RequestException,ValueError,TypeError):failures+=1;complete=False;break
            for group in data['jobs']:
                for job in group.get('jobs',[group]):
                    for href in [job.get('applyUrl',''),job.get('url','')]:
                        if board:=board_from_url(href):boards[board_key(board)]=board
                    domain=group.get('company',{}).get('domain') or job.get('companyDomain')
                    if domain and (url:=public_url('https://'+domain)):candidates.append(url)
            sequence=data.get('meta',{}).get('sequence')
            cache[cursor_key]=sequence
            if not sequence or not data['jobs']:cache.pop(cursor_key,None);break
        else:
            if sequence:complete=False
    return list(boards.values()),list(dict.fromkeys(candidates)),complete,failures

def listing_pages(source,settings,cache):
 api_failures=0
 if source=='sequoia':
  boards,candidates,complete,failures=sequoia_pages(settings,cache)
  if boards or candidates:return boards,candidates,complete,failures
  api_failures=failures
 spec=SOURCES[source];urls=[];boards={};candidates=[];complete=True;failures=api_failures
 if spec.get('param'):
  urls=[spec['url']+'?'+urlencode({spec['param']:term}) for term in TERMS]
 else:urls=[spec['url'],*spec.get('extra_pages',[])]
 for initial in urls:
  key='portfolio:page_cursor:'+initial;start=int(cache.get(key,1));url=page_url(initial,start) if start>1 else initial
  for _ in range(int(settings.get('max_pages_per_query',2))):
   try:
    result,state=cached_call(cache,'portfolio:listing:'+url,6,lambda:fetch_html(url))
    html,final=result
   except Exception as error:
    # Fetch/parser errors are visible, never successful empty results.
    failures+=1;complete=False;break
   anchors=links(html,final)
   for href,text in anchors:
    if board:=board_from_url(href):boards[board_key(board)]=board
    elif external(href,final) and re.search(r'apply|careers?|\bjobs\b',text+' '+href,re.I) and public_url(href):candidates.append(public_url(href))
   candidates.extend(company_profiles(html,final,source))
   next_links=[href for href,text in anchors if (text.strip().lower() in {'next','next page'} or text.strip()==str(start+1)) and urlparse(href).hostname==urlparse(final).hostname and urlparse(href).path==urlparse(final).path and parse_qs(urlparse(href).query).get('page')==[str(start+1)]]
   if not next_links:cache[key]=1;break
   start+=1;url=next_links[0];cache[key]=start;complete=False
 if spec.get('directory'):
  try:
   (html,final),_=cached_call(cache,'portfolio:directory:'+spec['directory'],24,lambda:fetch_html(spec['directory']))
   candidates.extend(company_profiles(html,final,source))
  except Exception:failures+=1
 # A known board with a client-rendered/blocked listing is incomplete even
 # when its public company directory provides working employer leads.
 if not boards and not candidates:complete=False
 return list(boards.values()),list(dict.fromkeys(candidates)),complete,failures

def discover(config,cache):
 if not config.get('enabled',False):return [],[]
 enabled=[s for s in config.get('sources',list(SOURCES)) if s in SOURCES]
 def process(source):
  spec=SOURCES[source];boards,candidates,complete,failures=listing_pages(source,config,cache)
  cap=max(1,int(config.get('employer_limits',{}).get(source,config.get('max_employers_per_source',8))));cursor=int(cache.get('portfolio:employer_cursor:'+source,0))
  rotated=candidates[cursor%len(candidates):]+candidates[:cursor%len(candidates)] if candidates else []
  selected=rotated[:cap];cache['portfolio:employer_cursor:'+source]=cursor+len(selected)
  def resolve(url):
   def fetch():
    # Directory profile pages identify the official company website first.
    if source in {'sequoia','yc'} and not external(url,spec.get('directory') or spec['url']):
     html,final=fetch_html(url)
     direct=[b for href,_ in links(html,final) if (b:=board_from_url(href))]
     if direct:return {'boards':direct,'complete':True}
     website=profile_website(html,final)
     if not website:return {'boards':[],'complete':False}
     return employer_boards({'url':website})
    return employer_boards({'url':url})
   try:result,_=cached_call(cache,'portfolio:employer:'+source+':'+url,6,fetch);return result
   except Exception:return {'boards':[],'complete':False}
  with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(resolve,selected))
  for result in results:boards.extend(result['boards'])
  unique={board_key(b):b for b in boards}
  origins={key:{'source':source,'url':spec['url']} for key in unique}
  deferred=max(0,len(candidates)-len(selected));unresolved=sum(not result['complete'] for result in results)
  status='ok' if complete and not deferred and not unresolved and not failures else 'partial'
  if not unique and not candidates:status='failed'
  health={'source':source,'label':spec['label']+' startup discovery','url':spec['url'],'status':status,'leads':len(unique),'employers_found':len(candidates),'employers_checked':len(selected),'deferred':deferred,'unresolved':unresolved,'listing_failures':failures}
  return list(unique.values()),origins,health
 with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(process,enabled))
 boards={};origins=cache.setdefault('portfolio:board_origins',{})
 for found,learned,_ in results:
  for b in found:boards[board_key(b)]=b
  for key,origin in learned.items():
   sources=origins.setdefault(key,{})
   sources[origin['source']]=origin['url']
 return list(boards.values()),[h for _,_,h in results]

def tag_jobs(jobs,cache):
 origins={}
 for key,sources in cache.get('portfolio:board_origins',{}).items():
  board=json.loads(key);cfg=board['config'];company=cfg.get('tenant') if isinstance(cfg,dict) else cfg
  origins[(board['source'],company.lower())]=sources
 for job in jobs:
  sources=origins.get((job.get('source'),str(job.get('company','')).lower()),{})
  if sources:
   job['discovery_sources']=list(dict.fromkeys([*job.get('discovery_sources',[]),*sources]))
   job.setdefault('discovery_urls',{}).update(sources)
