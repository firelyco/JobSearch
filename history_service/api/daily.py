"""Independent Vercel coordinator; GitHub runs remain workers, not the clock."""
import hmac,json,os,time
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from urllib.request import Request,urlopen
from urllib.error import HTTPError
try:
    from lib.daily_logic import decide,TZ
    from api.history import connect
except ImportError:
    from history_service.lib.daily_logic import decide,TZ
    from history_service.api.history import connect

REPO='firelyco/JobSearch'
SCHEMA='''CREATE TABLE IF NOT EXISTS daily_pipeline_runs (
 day DATE PRIMARY KEY, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());'''

def request_json(url,token='',payload=None):
    headers={'User-Agent':'JobSearch-Daily-Coordinator','Accept':'application/vnd.github+json'}
    if token:headers['Authorization']='Bearer '+token
    data=json.dumps(payload).encode() if payload is not None else None
    if data is not None:headers['Content-Type']='application/json'
    with urlopen(Request(url,data=data,headers=headers),timeout=5) as response:
        body=response.read(2_000_000)
        return json.loads(body) if body else {}

def contents(path,token):
    import base64
    try:
        data=request_json(f'https://api.github.com/repos/{REPO}/contents/{path}?ref=main',token)
        return json.loads(base64.b64decode(data['content']))
    except HTTPError as error:
        if error.code==404:return {}
        raise

def observe(token):
    from concurrent.futures import ThreadPoolExecutor
    def runs(workflow):
        data=request_json(f'https://api.github.com/repos/{REPO}/actions/workflows/{workflow}.yml/runs?per_page=10',token)
        return data.get('workflow_runs',[])
    with ThreadPoolExecutor(max_workers=4) as pool:
        a=pool.submit(contents,'docs/daily_batch.json',token);b=pool.submit(runs,'poll');c=pool.submit(runs,'fit');d=pool.submit(request_json,f'https://api.github.com/repos/{REPO}/contents/docs?ref=main',token)
        batch=a.result();workers={'poll':b.result(),'fit':c.result()};files={row['name']:row['sha'] for row in d.result()}
        if batch:
            expected=batch.get('input_blobs',{})
            batch['checkpoint_stale']=not expected or any(files.get(name)!=value for name,value in expected.items())
    published=False
    if batch.get('batch_id'):
        try:
            live=request_json('https://firelyco.github.io/JobSearch/daily_batch.json?t='+str(int(time.time())))
            published=live.get('batch_id')==batch['batch_id']
        except Exception:pass  # Publication is unknown, never assumed successful.
    return batch,workers,published

def run(now=None,dry_run=False):
    now=now or datetime.now(timezone.utc);day=now.astimezone(TZ).date().isoformat()
    token=os.environ.get('JOBSEARCH_GITHUB_TOKEN','')
    if not token:return {'day':day,'status':'configuration_required','reason':'Independent coordinator needs JOBSEARCH_GITHUB_TOKEN in Vercel Production.','checked_at':now.isoformat()}
    batch,workers,published=observe(token)
    from psycopg.types.json import Jsonb
    with connect() as db:
        db.execute(SCHEMA)
        # Atomic reservation survives overlapping cron invocations and crashes.
        db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('daily-pipeline:'+day,))
        row=db.execute('SELECT payload FROM daily_pipeline_runs WHERE day=%s',(day,)).fetchone()
        state=row[0] if row else {}
        result=decide(batch,workers,state,now,published=published)
        if not dry_run:
            db.execute('INSERT INTO daily_pipeline_runs(day,payload) VALUES (%s,%s) ON CONFLICT(day) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()',(day,Jsonb(result)))
    if result.get('action') and not dry_run:
        action=result['action'];inputs={'daily_only':'true','recovery_only':'true'} if action=='poll' else {'backfill':'true','provider':'nvidia','max':'24'}
        try:
            request_json(f'https://api.github.com/repos/{REPO}/actions/workflows/{action}.yml/dispatches',token,{'ref':'main','inputs':inputs})
        except Exception:
            # Keep the reservation. A timeout may have reached GitHub; wait for
            # run-list confirmation before retrying to avoid duplicate workers.
            result.update(status='dispatch_unconfirmed',reason='Worker dispatch could not be confirmed; the next check will reconcile before retrying.')
            with connect() as db:db.execute("UPDATE daily_pipeline_runs SET payload=%s,updated_at=now() WHERE day=%s AND payload->>'checked_at'=%s",(Jsonb(result),day,now.isoformat()))
    return result

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        supplied=self.headers.get('Authorization','');secret=os.environ.get('CRON_SECRET','')
        if not secret or not hmac.compare_digest(supplied.encode(),('Bearer '+secret).encode()):return self.respond(401,{'error':'Cron authentication required'})
        try:result=run(dry_run=parse_qs(urlparse(self.path).query).get('dry_run')==['1']);self.respond(200,result)
        except Exception:self.respond(503,{'status':'coordinator_unavailable','reason':'Coordinator could not confirm progress; completion has not been assumed.'})
    def respond(self,status,body):
        data=json.dumps(body).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
