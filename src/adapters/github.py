"""GitHub's public careers JSON feed; enumeration is independent of title filters."""
import logging
import requests
from . import JobBatch
from .public_json import get_json

log = logging.getLogger(__name__)
BASE = 'https://www.github.careers'

def fetch(_query=''):
    jobs = []; seen = set(); total = None; complete = False; pages = 0
    for page in range(1, 201):
        try:
            data=get_json(BASE+'/api/jobs',params={'limit':100,'page':page})
            if not isinstance(data.get('jobs'), list): raise ValueError('Listing schema changed')
            pages += 1; total = int(data['totalCount']); before = len(jobs)
            for item in data['jobs']:
                row = item.get('data', {}); ident = str(row.get('req_id') or row.get('slug') or '')
                if not ident or ident in seen: continue
                seen.add(ident)
                jobs.append({'source':'github','company':'github','id':ident,'title':row.get('title',''),
                    'location':row.get('location_name','')+' | '+row.get('country',''),
                    'url':BASE+'/experienced-professionals/jobs/'+ident,
                    'posted_at':row.get('posted_date',''),'updated_at':row.get('update_date',''),
                    'description':row.get('description','')})
            if len(jobs)>=total: complete=True; break
            if len(jobs)==before: raise ValueError('Pagination made no progress')
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            log.warning('github enumeration failed: %s',e); break
    if not complete: log.warning('github incomplete enumeration')
    return JobBatch(jobs,reported_total=total,collected=len(jobs),pages=pages,enumeration_complete=complete)

def fetch_detail(job):
    return next((j['description'] for j in fetch() if j['id']==str(job.get('id'))),'')
