"""Reconcile observed listings against intake; never infer internet-wide coverage."""
import re
from collections import Counter
from src import scorer, assessment_scope

def key(job):return ':'.join(str(job.get(k,'')) for k in ('source','company','id'))

def audit(raw, published, watchlist, role, health):
    raw={key(j):j for j in raw}; published={key(j):j for j in published}
    checks=[]
    for wanted in watchlist:
        companies={wanted['company'],*wanted.get('related_companies',[])}
        matches=[]
        for k,j in raw.items():
            if j.get('company','').lower() not in companies:continue
            if wanted.get('id'):
                match=str(j.get('id'))==wanted['id']
            else:
                match=bool(re.search(wanted['title_pattern'],j.get('title',''),re.I)
                           and re.search(wanted['location_pattern'],(j.get('resolved_locations') or j.get('location','')),re.I))
            if match:matches.append({'key':k,'title':j['title'],'url':j.get('url',''),
                'location':j.get('resolved_locations') or j.get('location',''),'published':k in published,
                'open_status':j.get('open_status','listed_in_feed'),
                'assessment_date_eligible':assessment_scope.allowed(j)})
        status='collected' if matches and wanted.get('id') else 'candidate_match_needs_exact_link' if matches else 'discovery_gap'
        if matches and not any(m['published'] for m in matches):status='screened_out'
        if matches and all(m['open_status']=='unverified_source_failure' for m in matches):status='retained_source_unverified'
        checks.append({'label':wanted['label'],'status':status,'matches':matches})
    dropped=[]
    for k,j in raw.items():
        if k in published or not re.search(r'technical.*program|engineering.*program|\bpmo\b|(?:program|project|portfolio)\s+management\s+office|program.*management|\btpm\b',j.get('title',''),re.I):continue
        score,reasons=scorer.score_job(j,role)
        dropped.append({'key':k,'title':j.get('title'),'location':j.get('location'),'reasons':reasons,'score':score})
    incomplete=[h for h in health if h.get('status') in {'failed','partial','limit_reached','not_configured','deferred'}]
    return {'scope':'Configured employer queries and observed listings; internet-wide completeness is unknown.',
            'internet_coverage_verified':False,'raw_unique':len(raw),'published_unique':len(published),
            'watchlist_counts':dict(Counter(c['status'] for c in checks)),
            'watchlist':checks,'screened_out_role_candidates':dropped,'incomplete_sources':incomplete}
