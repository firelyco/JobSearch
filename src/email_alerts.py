"""Read LinkedIn job-alert emails without changing mail; retain no raw mail.

Only employer/LinkedIn public job references enter discovery. Message identifiers
and scan progress live in the private history service, never Git or AI prompts.
"""
from __future__ import annotations
import email,imaplib,json,os,re,ssl
from collections import Counter
from datetime import datetime,timedelta,timezone
from email import policy
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import urlparse,parse_qs,unquote,parse_qsl,urlencode,urlunparse
import requests
from src import discovery,assessment_scope,scorer

MAX_MESSAGE=2_000_000
MONTHS=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
ROLE=re.compile(r'\b(?:jobs?|hiring|opportunit\w*|positions?|tpm|pmo)\b|program|portfolio|engineering manager',re.I)

def stamp(now=None):return (now or datetime.now(timezone.utc)).isoformat()
def job_key(job):return ':'.join(str(job.get(k,'')) for k in ('source','company','id'))
def clean(value,limit=200):return ' '.join(str(value or '').split())[:limit]

def linkedin_id(url):
    try:
        p=urlparse(url)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):return None
        if not p.hostname or not (p.hostname=='linkedin.com' or p.hostname.endswith('.linkedin.com')):return None
        match=re.search(r'/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d{6,20})(?:/|$)',unquote(p.path))
        value=match.group(1) if match else parse_qs(p.query).get('currentJobId',[''])[0] if '/jobs/' in p.path else ''
        return value if re.fullmatch(r'\d{6,20}',value) else None
    except (ValueError,TypeError):return None

def employer_url(url,depth=0):
    # Only recognized public ATS/employer career URLs; never follow email trackers.
    if depth>2:return ''
    try:
        p=urlparse(url)
        if p.hostname and (p.hostname=='linkedin.com' or p.hostname.endswith('.linkedin.com')):
            for name in ('url','redirect','destination'):
                target=parse_qs(p.query).get(name,[''])[0]
                if target:return employer_url(target,depth+1)
            return ''
        normalized=discovery.canonical_url(url)
        p=urlparse(normalized)
        # Drop tracking/recipient parameters before private storage or publication.
        query=[(k,v) for k,v in parse_qsl(p.query) if k in {'gh_jid','for','jobId','jobid','job_id','requisitionId'} and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',v)]
        normalized=urlunparse((p.scheme,p.netloc,p.path,'',urlencode(query),''))
        p=urlparse(normalized)
        if p.port not in (None,443):return ''
        if discovery.board_from_url(normalized):return normalized[:1000]
        if re.fullmatch(r'(?:careers|jobs|apply\.careers)\.[a-z0-9-]+\.(?:com|org|io|ai|co|net)',p.hostname or ''):
            return normalized[:1000]
    except (ValueError,TypeError):pass
    return ''

class Node:
    def __init__(self,tag='',attrs=None,parent=None):self.tag=tag;self.attrs=attrs or {};self.parent=parent;self.children=[]
    def text(self):return ''.join(c.text() if isinstance(c,Node) else c for c in self.children)
    def links(self):
        yield from [self] if self.tag=='a' else []
        for child in self.children:
            if isinstance(child,Node):yield from child.links()
    def lines(self):
        parts=[]
        for child in self.children:
            if isinstance(child,Node):
                text=child.lines()
                parts.append(('\n' if child.tag in {'p','div','tr','td','br','li','a'} else '')+text+'\n')
            else:parts.append(child)
        return ''.join(parts)

class AlertHTML(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.root=Node();self.current=self.root;self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.skip+=1;return
        if self.skip:return
        node=Node(tag,dict(attrs),self.current);self.current.children.append(node)
        if tag not in {'br','img','meta','link','hr','input','wbr','source','area','base','embed'}:self.current=node
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.skip=max(0,self.skip-1);return
        if self.skip:return
        node=self.current
        while node.parent is not None:
            if node.tag==tag:self.current=node.parent;return
            node=node.parent
    def handle_data(self,text):
        if not self.skip:self.current.children.append(text)

def parse_html(text):
    parser=AlertHTML();parser.feed(text);rows={}
    for anchor in parser.root.links():
        identifier=linkedin_id(anchor.attrs.get('href',''))
        if not identifier:continue
        title=clean(anchor.text())
        if not ROLE.search(title):title=clean(anchor.attrs.get('aria-label') or anchor.attrs.get('title') or title)
        if not ROLE.search(title) or title.lower() in {'view job','view jobs','see job','see jobs','apply','apply now'}:title=''
        card=anchor.parent;chosen=card
        while card and card.parent:
            ids={linkedin_id(a.attrs.get('href','')) for a in card.links()}-{None}
            if len(ids)>1:break
            if len(card.text())<1800:chosen=card
            card=card.parent
        company='';location='';urls=[]
        if chosen:
            lines=[clean(line) for line in chosen.lines().splitlines() if clean(line)]
            if title in lines:
                after=lines[lines.index(title)+1:]
                for line in after:
                    if line==title or re.search(r'apply|view job|saved|applicant|promoted|ago|recommended|your job alert|unsubscribe|privacy|sent to|@|©',line,re.I):continue
                    bits=re.split(r'\s+[·|]\s+',line,1)
                    company=bits[0][:120];location=bits[1][:160] if len(bits)>1 else '';break
            urls=list(dict.fromkeys(u for a in chosen.links() if (u:=employer_url(a.attrs.get('href','')))))[:2]
        row={'linkedin_id':identifier,'url':'https://www.linkedin.com/jobs/view/'+identifier,'title':title if '@' not in title else '', 'company':company,'location':location,'employer_urls':urls}
        previous=rows.get(identifier)
        if previous:
            for field in ('title','company','location'):
                if row[field] and not previous[field]:previous[field]=row[field]
            previous['employer_urls']=list(dict.fromkeys(previous['employer_urls']+urls))[:2]
        else:rows[identifier]=row
    return list(rows.values())

def body_parts(message):
    if message.get_content_disposition()=='attachment' or message.get_filename():return
    if message.is_multipart():
        if message.get_content_type().startswith('multipart/'):
            for part in message.iter_parts():yield from body_parts(part)
    elif message.get_content_type() in {'text/plain','text/html'}:
        data=message.get_payload(decode=True) or b''
        if len(data)>MAX_MESSAGE:raise ValueError('Message too large')
        try:text=data.decode(message.get_content_charset() or 'utf-8',errors='replace')
        except LookupError:text=data.decode('utf-8',errors='replace')
        yield message.get_content_type(),text

def parse_message(raw):
    if len(raw)>MAX_MESSAGE:raise ValueError('Message too large')
    message=email.message_from_bytes(raw,policy=policy.default)
    sender=parseaddr(str(message.get('From','')))[1].lower()
    domain=sender.rsplit('@',1)[-1]
    if not (domain=='linkedin.com' or domain.endswith('.linkedin.com')):return []
    rows={}
    for kind,text in body_parts(message):
        if kind=='text/html':found=parse_html(text)
        else:
            found=[]
            lines=text.splitlines()
            for index,line in enumerate(lines):
                for url in re.findall(r'https://[^\s<>"\]]+',line):
                    identifier=linkedin_id(url.rstrip(').,'))
                    if not identifier:continue
                    prior=[clean(v) for v in lines[max(0,index-3):index] if clean(v)]
                    title=next((v for v in prior if ROLE.search(v) and '@' not in v),'')
                    found.append({'linkedin_id':identifier,'url':'https://www.linkedin.com/jobs/view/'+identifier,
                        'title':title,'company':'','location':'','employer_urls':[]})
        for row in found:
            previous=rows.setdefault(row['linkedin_id'],row)
            for field in ('title','company','location'):
                if not previous[field] and row[field]:previous[field]=row[field]
            previous['employer_urls']=list(dict.fromkeys(previous['employer_urls']+row['employer_urls']))[:2]
    return list(rows.values())

def load_state():
    from src.private_fit_store import _send,URL
    token=os.environ.get('HISTORY_SCORER_TOKEN','')
    if not token:raise RuntimeError('Private email state is not configured')
    response=_send(requests.get,headers={'Authorization':'Bearer '+token},params={'action':'email_alert_read'},timeout=40)
    if response.status_code!=200:raise RuntimeError('Private email state unavailable')
    return response.json().get('state') or {'schema':1,'messages':{},'leads':{}}

def save_state(state):
    from src.private_fit_store import _send
    token=os.environ.get('HISTORY_SCORER_TOKEN','')
    if not token:raise RuntimeError('Private email state is not configured')
    response=_send(requests.post,headers={'Authorization':'Bearer '+token},json={'action':'email_alert_write','state':state},timeout=40)
    if response.status_code!=200:raise RuntimeError('Private email state unavailable')

class MailAccessError(RuntimeError):
    """Keep only allowlisted protocol diagnostics, never raw server text."""
    def __init__(self,phase,error=None):
        self.phase=phase
        text=str(error or '').upper()
        codes={'AUTHENTICATIONFAILED','AUTHORIZATIONFAILED','UNAVAILABLE','PRIVACYREQUIRED','ALERT','LOGINDISABLED','LIMIT'}
        self.response_code=next((code for code in sorted(codes) if '['+code+']' in text),'not_provided')
        self.category=('app_password_required' if re.search(r'APP(?:LICATION)?[- ](?:SPECIFIC[- ])?PASSWORD',text) else
            'temporarily_blocked' if re.search(r'TOO MANY|RATE LIMIT|TRY AGAIN|TEMPORAR',text) else
            'authentication_rejected' if phase=='sign_in' else 'operation_failed')
        super().__init__(phase)

def fetch_yahoo(state,*,now=None,factory=None,max_messages=200,lookback_days=14):
    now=now or datetime.now(timezone.utc)
    counters={'messages_checked':0,'alerts_with_jobs':0,'job_references_found':0,'failed_messages':0,'alerts_without_job_links':0}
    client=None;phase='connection'
    try:
        client=(factory or imaplib.IMAP4_SSL)('imap.mail.yahoo.com',993,ssl_context=ssl.create_default_context(),timeout=30)
        phase='sign_in'
        client.login(os.environ['YAHOO_EMAIL'].strip(),os.environ['YAHOO_APP_PASSWORD'])
        phase='mailbox'
        folder=os.environ.get('JOBSEARCH_ALERT_MAILBOX','INBOX')
        if any(c in folder for c in '\r\n'):raise ValueError('Invalid mailbox')
        quoted='"'+folder.replace('\\','\\\\').replace('"','\\"')+'"'
        result,_=client.select(quoted,readonly=True)
        if result!='OK':raise RuntimeError('Mailbox unavailable')
        phase='mailbox_identity'
        _,validity=client.response('UIDVALIDITY')
        if not validity or not validity[0]:raise RuntimeError('Mailbox identity unavailable')
        validity=validity[0].decode('ascii');since=now-timedelta(days=lookback_days)
        date=f'{since.day:02d}-{MONTHS[since.month-1]}-{since.year}'
        phase='search'
        result,data=client.uid('search',None,f'(SINCE "{date}" FROM "linkedin.com")')
        if result!='OK':raise RuntimeError('Mail search failed')
        messages=state.setdefault('messages',{});leads=state.setdefault('leads',{})
        folder=os.environ.get('JOBSEARCH_ALERT_MAILBOX','INBOX')
        import hashlib
        prefix='v1:'+hashlib.sha256((folder+':'+validity).encode()).hexdigest()[:16]+':'
        unseen=[uid for uid in data[0].split() if prefix+uid.decode('ascii') not in messages]
        unparsed=state.setdefault('unparsed',{})
        for uid in unseen[:max_messages]:
            try:
                result,parts=client.uid('fetch',uid,'(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])')
                if result!='OK':raise RuntimeError('Header unavailable')
                pair=next(p for p in parts if isinstance(p,tuple));size=re.search(rb'RFC822.SIZE (\d+)',pair[0])
                header=email.message_from_bytes(pair[1],policy=policy.default)
                sender=parseaddr(str(header.get('From','')))[1].lower();domain=sender.rsplit('@',1)[-1]
                legitimate=domain=='linkedin.com' or domain.endswith('.linkedin.com')
                is_alert=legitimate and ('job' in sender.split('@')[0] or ROLE.search(str(header.get('Subject',''))))
                if size and int(size.group(1))>MAX_MESSAGE:raise ValueError('Message too large')
                if is_alert:
                    result,parts=client.uid('fetch',uid,'(BODY.PEEK[])')
                    if result!='OK':raise RuntimeError('Message unavailable')
                    raw=next(p[1] for p in parts if isinstance(p,tuple))
                    found=parse_message(raw)
                    if len(leads)+len({r['linkedin_id'] for r in found}-leads.keys())>1500:raise ValueError('Reference retention limit')
                    counters['alerts_with_jobs']+=bool(found);counters['job_references_found']+=len(found)
                    counters['alerts_without_job_links']+=not bool(found)
                    marker=prefix+uid.decode('ascii')
                    if found:unparsed.pop(marker,None)
                    else:unparsed[marker]=stamp(now)
                    for row in found:
                        old=leads.get(row['linkedin_id'],{})
                        leads[row['linkedin_id']]={**old,**{k:v for k,v in row.items() if v},
                            'first_alert_at':old.get('first_alert_at',stamp(now)),'last_alert_at':stamp(now)}
                messages[prefix+uid.decode('ascii')]=stamp(now);counters['messages_checked']+=1
            except (imaplib.IMAP4.error,OSError,RuntimeError,ValueError,StopIteration,TypeError):
                counters['failed_messages']+=1
        counters['messages_deferred']=max(0,len(unseen)-max_messages)
        counters['unresolved_alert_messages']=len(unparsed)
        state['last_checked_at']=stamp(now)
        if not counters['failed_messages'] and not counters['messages_deferred'] and not counters['unresolved_alert_messages']:state['last_success_at']=stamp(now)
        # Message dedupe is private; old identifiers can expire after the overlap window.
        horizon=stamp(now-timedelta(days=max(30,lookback_days+7)))
        state['messages']={k:v for k,v in messages.items() if v>=horizon}
        state['unparsed']={k:v for k,v in unparsed.items() if v>=horizon}
        return {**counters,'status':'partial' if counters['failed_messages'] or counters['messages_deferred'] or counters['unresolved_alert_messages'] else 'ok','lookback_days':lookback_days}
    except (imaplib.IMAP4.error,OSError,RuntimeError,ValueError,UnicodeError) as error:
        raise MailAccessError(phase,error) from None
    finally:
        if client:
            try:client.logout()
            except (imaplib.IMAP4.error,OSError):pass

def prepare(previous,config,*,now=None):
    now=now or datetime.now(timezone.utc)
    health={'source':'linkedin_email','label':'LinkedIn job-alert emails','status':'not_configured','jobs':0}
    if not os.environ.get('YAHOO_EMAIL') or not os.environ.get('YAHOO_APP_PASSWORD'):
        return None,health,[],[]
    try:
        state=load_state()
        try:health.update(fetch_yahoo(state,now=now,max_messages=int(config.get('max_messages_per_run',200)),lookback_days=int(config.get('lookback_days',14))))
        except MailAccessError as error:
            reasons={'connection':'Yahoo IMAP could not be reached over TLS.',
                'sign_in':'Yahoo rejected this IMAP sign-in. This does not establish that the Yahoo website credentials are incorrect; third-party authorization may be required.',
                'mailbox':'Yahoo sign-in succeeded but the selected mailbox could not be opened read-only.',
                'mailbox_identity':'Yahoo sign-in succeeded but mailbox UIDVALIDITY was not available.',
                'search':'Yahoo sign-in and mailbox access succeeded but the LinkedIn alert search failed.'}
            health.update(status='failed',failure_stage=error.phase,response_code=error.response_code,failure_category=error.category,reason=reasons.get(error.phase,'Yahoo scan could not complete.'))
        rows=list(state.get('leads',{}).values())
        health['jobs']=len(rows);health['last_success_at']=state.get('last_success_at')
        urls=[];queries=[];known={discovery.canonical_url(j.get('url','')) for j in previous}
        for row in sorted(rows,key=lambda r:r.get('first_alert_at',''),reverse=True):
            urls.extend(row.get('employer_urls',[]))
            if any(u in known for u in [row['url'],*row.get('employer_urls',[])]):continue
            if not os.environ.get('BRAVE_SEARCH_API_KEY') or len(queries)>=int(config.get('max_resolution_queries',10)):continue
            if row.get('resolution_attempts',0)>=3 or row.get('resolution_checked_at','')>stamp(now-timedelta(hours=24)):continue
            if row.get('title') and row.get('company'):
                query='"'+row['title'].replace('"','')+'" "'+row['company'].replace('"','')+'" careers'
            else:query='"'+row['linkedin_id']+'" job careers'
            queries.append(query);row['resolution_attempts']=row.get('resolution_attempts',0)+1;row['resolution_checked_at']=stamp(now)
        save_state(state)
        return state,health,list(dict.fromkeys(urls)),list(dict.fromkeys(queries))
    except (requests.RequestException,RuntimeError,ValueError,KeyError):
        # Never leak provider exception text, recipients, headers, or credentials.
        health.update(status='failed',reason='Private scan state could not be read or saved; the scan will retry without changing any messages.')
        return None,health,[],[]

def report(state,health,raw,published,scores,policy,role,*,previous=None,now=None):
    from src import fit_backfill as b
    checked=stamp(now);rows=[];published_keys={job_key(j) for j in published}
    raw=list({job_key(j):j for j in raw}.values())
    leads=state.get('leads',{}).values() if state is not None else (previous or {}).get('rows',[])
    normalize=lambda value:re.sub(r'[^a-z0-9]','',str(value).lower())
    def company_key(value):
        value=re.sub(r'(?:\.com|\bincorporated|\binc\.?|\bcorporation|\bcorp\.?|\bllc|\bltd\.?|careers|jobs)\s*$','',str(value).lower()).strip()
        return normalize(value)
    for lead in leads:
        identifier=lead.get('linkedin_id');urls=set(lead.get('employer_urls',[]))|{lead['url']}
        exact=[j for j in raw if discovery.canonical_url(j.get('url','')) in urls or identifier and linkedin_id(j.get('url',''))==identifier]
        possible=[]
        if not exact and lead.get('title') and lead.get('company'):
            possible=[j for j in raw if normalize(j.get('title'))==normalize(lead['title']) and company_key(j.get('company'))==company_key(lead['company'])]
        matches=exact or possible
        collection='tracked' if exact else 'possible_match' if possible else 'missing'
        if exact and not any(job_key(j) in published_keys for j in exact):collection='screened_out'
        assessment='not_assessed';verdicts=[]
        for job in matches:
            score=scores.get(job_key(job),{})
            if not assessment_scope.allowed(job):assessment='outside_date_scope'
            elif score.get('recommendation') in b.FINAL and score.get('validated') is True and b.fresh(job,score,policy.get('context_hash')):
                assessment='assessed';verdicts.append({'strong':'Strong','medium':'Middle','not':'Pass'}[score['recommendation']])
            elif score:assessment='reassessment_pending'
        rows.append({k:lead[k] for k in ('linkedin_id','url','title','company','location','employer_urls','first_alert_at','last_alert_at') if k in lead}|
            {'collection':collection,'assessment':assessment,'verdicts':sorted(set(verdicts)),
             'matches':[{'key':job_key(j),'title':j.get('title',''),'company':j.get('company',''),'url':j.get('url',''),'assessment_date_eligible':assessment_scope.allowed(j)} for j in matches],
             'reason':'Title/company similarity is not proof of the same requisition.' if possible else
             '; '.join(scorer.score_job(exact[0],role)[1]) if collection=='screened_out' else
             'Employer URL still needs resolution; this is a lead, not an assessed job.' if not matches else ''})
    return {'schema':1,'checked_at':checked,'connection':health,'last_success_at':state.get('last_success_at') if state else (previous or {}).get('last_success_at'),
        'scope':'LinkedIn job links found in alert emails; does not verify all LinkedIn or in-app listings.',
        'counts':dict(Counter(r['collection'] for r in rows)),'rows':rows}


def main():
    """Manual read-only connection/import check; daily collection stays in poll."""
    from pathlib import Path
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--connection-only',action='store_true');args=parser.parse_args()
    if args.connection_only:
        try:
            result=fetch_yahoo({'schema':1,'messages':{},'leads':{}},max_messages=0)
            print(json.dumps({'connection':'ok','matched_linkedin_messages':result['messages_deferred'],'message_bodies_read':0,'scan_state_saved':False}))
            return 0
        except MailAccessError as error:
            print(json.dumps({'connection':'failed','failure_stage':error.phase,'response_code':error.response_code,'failure_category':error.category,'message_bodies_read':0,'scan_state_saved':False}))
            return 1
    import yaml
    root=Path(__file__).resolve().parents[1]
    def read(name,default):
        try:return json.loads((root/name).read_text(encoding='utf-8'))
        except (OSError,ValueError):return default
    config=read('config/email_alerts.json',{})
    config['max_resolution_queries']=0  # A scan check never consumes search budget.
    jobs=read('docs/jobs.json',[])
    state,health,_,_=prepare(jobs,config)
    result=report(state,health,jobs,jobs,read('docs/fit_scores.json',{}),read('docs/fit_policy.json',{}),
        yaml.safe_load((root/'config/role_config.yml').read_text(encoding='utf-8')),
        previous=read('docs/email_alerts.json',{}))
    (root/'docs/email_alerts.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'connection':health,'job_reference_counts':result['counts']}))
    return 0 if health['status'] in {'ok','partial'} else 1

if __name__=='__main__':raise SystemExit(main())
