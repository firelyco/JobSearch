"""Microsoft public PCSX search. Search result coverage is not whole-company coverage."""
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, quote
import requests
from . import JobBatch
from .public_json import get_json

log = logging.getLogger(__name__)
BASE = 'https://apply.careers.microsoft.com'

def stamp(value):
    try: return datetime.fromtimestamp(int(value),timezone.utc).isoformat() if value else ''
    except (TypeError, ValueError, OverflowError): return ''

def fetch(query):
    settings=query if isinstance(query,dict) else {'query':query}
    query=settings.get('query','')
    jobs=[]; seen=set(); total=None; complete=False; pages=0; start=0
    for _ in range(200):
        try:
            # The public endpoint accepts percent-encoded spaces (not form '+').
            params={'domain':'microsoft.com','query':query,'start':start}
            for field,values in settings.get('filters',{}).items():
                params['filter_'+field]=values
            params=urlencode(params,doseq=True,quote_via=quote)
            if pages:time.sleep(2)
            envelope=get_json(BASE+'/api/pcsx/search?'+params); data=envelope.get('data',{})
            if not isinstance(data.get('positions'),list) or envelope.get('status')!=200:
                raise ValueError('Listing schema changed')
            total=int(data['count']); pages+=1; before=len(jobs)
            for row in data['positions']:
                ident=str(row.get('id') or '')
                if not ident or ident in seen:continue
                seen.add(ident)
                jobs.append({'source':'microsoft','company':'microsoft','id':ident,'title':row.get('name',''),
                    'location':' | '.join(row.get('locations',[])),
                    'url':BASE+'/careers/job/'+ident,'posted_at':stamp(row.get('postedTs')),'updated_at':''})
            if len(jobs)>=total:complete=True;break
            if len(jobs)==before:raise ValueError('Pagination made no progress')
            start+=len(data['positions'])
        except (requests.RequestException,ValueError,KeyError,TypeError) as e:
            log.warning('microsoft enumeration failed: %s',e);break
    if not complete:log.warning('microsoft incomplete enumeration')
    return JobBatch(jobs,reported_total=total,collected=len(jobs),pages=pages,enumeration_complete=complete,query=query)

def fetch_detail(job):
    from src.discovery import jobs_from_page
    try:
        r=requests.get(job['url'],timeout=20);r.raise_for_status()
        jobs,_=jobs_from_page(r.text,job['url'])
        return next((j.get('description','') for j in jobs),'')
    except (requests.RequestException,ValueError,KeyError):return ''
