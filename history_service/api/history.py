"""Private single-owner application history API backed by PostgreSQL."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ALLOWED_STATUSES = {'new', 'applied', 'interviewing', 'rejected', 'not-interested'}
MAX_BODY = 2_000_000
SESSION_SECONDS = 12 * 3600
SCHEMA = '''
CREATE TABLE IF NOT EXISTS email_alert_state (
 id TEXT PRIMARY KEY, payload JSONB NOT NULL, saved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidate_evidence (
 inventory_hash TEXT PRIMARY KEY, payload JSONB NOT NULL, saved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fit_assessments (
 job_key TEXT NOT NULL, input_hash TEXT NOT NULL, inventory_hash TEXT NOT NULL REFERENCES candidate_evidence(inventory_hash),
 payload JSONB NOT NULL, saved_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(job_key,input_hash)
);

CREATE TABLE IF NOT EXISTS application_records (
  job_key TEXT PRIMARY KEY, status TEXT NOT NULL, application JSONB,
  updated_at BIGINT NOT NULL, mutation_id TEXT NOT NULL, deleted BOOLEAN NOT NULL DEFAULT FALSE,
  saved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS application_events (
  job_key TEXT NOT NULL, mutation_id TEXT NOT NULL, payload JSONB NOT NULL,
  saved_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(job_key, mutation_id)
);
CREATE TABLE IF NOT EXISTS history_login_attempts (
  id BIGSERIAL PRIMARY KEY, address_hash TEXT NOT NULL, attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS history_login_address_time ON history_login_attempts(address_hash, attempted_at);
'''

def connect():
    import psycopg
    url = os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL')
    if not url:
        raise RuntimeError('Database is not configured')
    return psycopg.connect(url, connect_timeout=10, sslmode='require')

def initialize(connection):
    connection.execute(SCHEMA)

def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')

def session_secret():
    value = os.environ.get('HISTORY_SESSION_SECRET', '')
    if len(value) < 32:
        raise RuntimeError('Session signing is not configured')
    return value.encode()

def issue_session(now=None):
    payload = b64(json.dumps({'owner':'default', 'exp':int(now or time.time()) + SESSION_SECONDS,
        'nonce':secrets.token_hex(16)}, separators=(',', ':')).encode())
    signature = b64(hmac.new(session_secret(), payload.encode(), hashlib.sha256).digest())
    return payload + '.' + signature

def verify_session(token, now=None):
    try:
        payload, signature = token.split('.')
        expected = b64(hmac.new(session_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return False
        record = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        return record.get('owner') == 'default' and record.get('exp',0) > (now or time.time())
    except (ValueError, TypeError, KeyError):
        return False

def validate_records(records):
    if not isinstance(records, dict) or len(records) > 3000:
        raise ValueError('Invalid application records')
    for key, entry in records.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 500 or not isinstance(entry, dict):
            raise ValueError('Invalid record key')
        if entry.get('status') not in ALLOWED_STATUSES:
            raise ValueError('Invalid application status')
        if type(entry.get('updated_at')) is not int or not 0 <= entry['updated_at'] <= int(time.time()*1000)+300000:
            raise ValueError('Invalid update timestamp')
        if not isinstance(entry.get('mutation_id'), str) or not 1 <= len(entry['mutation_id']) <= 128:
            raise ValueError('Invalid mutation identifier')
        if type(entry.get('deleted',False)) is not bool:
            raise ValueError('Invalid removal flag')
        application = entry.get('application')
        if application is not None:
            if not isinstance(application,dict) or len(json.dumps(application)) > 30000:
                raise ValueError('Invalid application details')
            for field in ('title','company','location','url','source','posted_at','fit','applied_at','notes'):
                if field in application and (not isinstance(application[field],str) or len(application[field]) > 10000):
                    raise ValueError('Invalid application field')
            if application.get('url') and not application['url'].startswith('https://'):
                raise ValueError('Application links must use HTTPS')
    return records

def read_records(connection):
    rows = connection.execute('SELECT job_key,status,application,updated_at,mutation_id,deleted FROM application_records').fetchall()
    return {key:{'status':status,'application':application,'updated_at':updated,'mutation_id':mutation,'deleted':deleted}
        for key,status,application,updated,mutation,deleted in rows}

def save_records(connection, records):
    from psycopg.types.json import Jsonb
    validate_records(records)
    for key, entry in records.items():
        changed = connection.execute('''INSERT INTO application_records(job_key,status,application,updated_at,mutation_id,deleted)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(job_key) DO UPDATE SET status=EXCLUDED.status, application=EXCLUDED.application,
            updated_at=EXCLUDED.updated_at, mutation_id=EXCLUDED.mutation_id, deleted=EXCLUDED.deleted, saved_at=now()
            WHERE (EXCLUDED.updated_at,EXCLUDED.mutation_id) > (application_records.updated_at,application_records.mutation_id)
            RETURNING job_key''', (key,entry['status'],Jsonb(entry.get('application')),entry['updated_at'],entry['mutation_id'],entry.get('deleted',False))).fetchone()
        if changed:
            connection.execute('INSERT INTO application_events(job_key,mutation_id,payload) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                (key,entry['mutation_id'],Jsonb(entry)))
    return read_records(connection)

def validate_email_state(state):
    """Private scan state accepts job references/opaque UIDs, never raw mail."""
    if not isinstance(state,dict) or set(state)-{'schema','messages','unparsed','leads','last_checked_at','last_success_at'}:
        raise ValueError('Invalid email state')
    if state.get('schema')!=1:raise ValueError('Invalid email state version')
    messages=state.get('messages',{});leads=state.get('leads',{})
    if not isinstance(messages,dict) or not isinstance(leads,dict) or len(messages)>10000 or len(leads)>1500:
        raise ValueError('Email state limit')
    unparsed=state.get('unparsed',{})
    if not isinstance(unparsed,dict) or len(unparsed)>10000:raise ValueError('Invalid unparsed alert state')
    for key,value in {**messages,**unparsed}.items():
        if not isinstance(key,str) or len(key)>80 or not isinstance(value,str) or len(value)>40:raise ValueError('Invalid mail reference')
    allowed={'linkedin_id','url','title','company','location','employer_urls','first_alert_at','last_alert_at','resolution_attempts','resolution_checked_at'}
    for key,row in leads.items():
        if not isinstance(row,dict) or set(row)-allowed or key!=row.get('linkedin_id') or not key.isdigit() or not 6<=len(key)<=20:
            raise ValueError('Invalid job reference')
        if row.get('url')!='https://www.linkedin.com/jobs/view/'+key:raise ValueError('Invalid LinkedIn URL')
        for field,value in row.items():
            if field=='employer_urls':
                if not isinstance(value,list) or len(value)>2 or any(not isinstance(u,str) or len(u)>1000 or not u.startswith('https://') for u in value):raise ValueError('Invalid employer URL')
            elif field=='resolution_attempts':
                if type(value) is not int or not 0<=value<=3:raise ValueError('Invalid resolution attempts')
            elif not isinstance(value,str) or len(value)>1000:raise ValueError('Invalid reference field')
    if len(json.dumps(state))>1_800_000:raise ValueError('Email state limit')
    return state


class handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass  # Do not log passwords, tokens or application payloads.

    def allowed_origin(self):
        origin = self.headers.get('Origin','')
        allowed = {s.strip() for s in os.environ.get('HISTORY_ALLOWED_ORIGINS','https://firelyco.github.io').split(',')}
        return not origin or origin in allowed

    def respond(self,status,body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(payload)))
        if self.headers.get('Origin') and self.allowed_origin():
            self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Vary','Origin')
            self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.respond(200 if self.allowed_origin() else 403,{})

    def do_GET(self):
        self.process_history_request()

    def do_POST(self):
        self.process_history_request()

    def process_history_request(self):
        if not self.allowed_origin():
            self.respond(403,{'error':'Origin is not allowed'}); return
        try:
            body = {}
            if self.command == 'POST':
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= MAX_BODY:
                    self.respond(413,{'error':'Request is too large or empty'}); return
                body = json.loads(self.rfile.read(size))
                if not isinstance(body,dict):
                    raise ValueError('Invalid request')
            action=body.get('action') or parse_qs(urlparse(self.path).query).get('action',[''])[0]
            if action in {'email_alert_read','email_alert_write'}:
                machine=os.environ.get('HISTORY_SCORER_TOKEN','')
                supplied=self.headers.get('Authorization','')
                if not machine or not hmac.compare_digest(supplied.encode(),('Bearer '+machine).encode()):
                    self.respond(401,{'error':'Machine authentication required'});return
                if self.command!=('GET' if action=='email_alert_read' else 'POST'):
                    self.respond(405,{'error':'Method not allowed'});return
                state=validate_email_state(body.get('state')) if action=='email_alert_write' else None
                with connect() as connection:
                    initialize(connection)
                    if state is not None:
                        from psycopg.types.json import Jsonb
                        connection.execute("INSERT INTO email_alert_state(id,payload) VALUES ('linkedin',%s) ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload,saved_at=now()",(Jsonb(state),))
                    row=connection.execute("SELECT payload FROM email_alert_state WHERE id='linkedin'").fetchone()
                self.respond(200,{'state':row[0] if row else {'schema':1,'messages':{},'leads':{}}});return
            if body.get('action') in {'assessment_write','inventory_write'}:
                machine = os.environ.get('HISTORY_SCORER_TOKEN','')
                supplied = self.headers.get('Authorization','')
                if not machine or not hmac.compare_digest(supplied.encode(),('Bearer '+machine).encode()):
                    self.respond(401,{'error':'Scorer authentication required'});return
                from psycopg.types.json import Jsonb
                payload=body.get('payload',{})
                inventory=payload.get('inventory')
                if not isinstance(inventory,list) or not inventory or len(inventory)>1000:
                    raise ValueError('Invalid inventory')
                inventory_hash=hashlib.sha256(json.dumps(inventory,sort_keys=True).encode()).hexdigest()
                with connect() as connection:
                    initialize(connection)
                    connection.execute('INSERT INTO candidate_evidence(inventory_hash,payload) VALUES (%s,%s) ON CONFLICT DO NOTHING',(inventory_hash,Jsonb(inventory)))
                    if body['action']=='assessment_write':
                        fingerprint=payload.get('input_hash','')
                        key=body.get('job_key','')
                        if len(fingerprint)!=64 or not key or len(key)>500:raise ValueError('Invalid assessment')
                        stored={k:v for k,v in payload.items() if k!='inventory'}
                        connection.execute('INSERT INTO fit_assessments(job_key,input_hash,inventory_hash,payload) VALUES (%s,%s,%s,%s) ON CONFLICT(job_key,input_hash) DO UPDATE SET payload=EXCLUDED.payload,saved_at=now()',
                            (key,fingerprint,inventory_hash,Jsonb(stored)))
                self.respond(200,{'saved':True});return
            if body.get('action') == 'login':
                password = body.get('password','')
                expected = os.environ.get('HISTORY_PASSWORD','')
                if not expected:
                    raise RuntimeError('Sign-in is not configured')
                address = self.headers.get('x-forwarded-for','').split(',')[0] or self.client_address[0]
                address_hash = hmac.new(session_secret(),address.encode(),hashlib.sha256).hexdigest()
                with connect() as connection:
                    initialize(connection)
                    # Serialize attempts for an address before checking limits.
                    connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(address_hash,))
                    connection.execute("DELETE FROM history_login_attempts WHERE attempted_at < now() - interval '1 day'")
                    attempts=connection.execute("SELECT count(*) FROM history_login_attempts WHERE address_hash=%s AND attempted_at > now() - interval '15 minutes'",(address_hash,)).fetchone()[0]
                    if attempts >= 15:
                        self.respond(429,{'error':'Too many sign-in attempts. Try again in 15 minutes.'}); return
                    connection.execute('INSERT INTO history_login_attempts(address_hash) VALUES (%s)',(address_hash,))
                if not isinstance(password,str) or not hmac.compare_digest(password.encode(),expected.encode()):
                    self.respond(401,{'error':'Incorrect password'}); return
                self.respond(200,{'token':issue_session(),'expires_in':SESSION_SECONDS}); return
            authorization = self.headers.get('Authorization','')
            query=parse_qs(urlparse(self.path).query)
            scorer=os.environ.get('HISTORY_SCORER_TOKEN','')
            machine_read=bool(self.command=='GET' and query.get('action')==['assessment'] and scorer and hmac.compare_digest(authorization.encode(),('Bearer '+scorer).encode()))
            if not machine_read and (not authorization.startswith('Bearer ') or not verify_session(authorization[7:])):
                self.respond(401,{'error':'Sign in to access private application history'}); return
            query=parse_qs(urlparse(self.path).query)
            if self.command=='GET' and query.get('action')==['assessment']:
                key=query.get('job_key',[''])[0];fingerprint=query.get('input_hash',[''])[0]
                with connect() as connection:
                    initialize(connection)
                    row=connection.execute('SELECT f.payload,e.payload FROM fit_assessments f JOIN candidate_evidence e USING(inventory_hash) WHERE job_key=%s AND (input_hash=%s OR %s) ORDER BY f.saved_at DESC LIMIT 1',(key,fingerprint, bool(machine_read and not fingerprint))).fetchone()
                if not row:self.respond(404,{'error':'This assessment is not available yet'});return
                self.respond(200,{'assessment':{**row[0],'inventory':row[1]}});return
            with connect() as connection:
                initialize(connection)
                records = save_records(connection,body.get('records')) if self.command == 'POST' else read_records(connection)
            self.respond(200,{'version':1,'records':records})
        except (ValueError,TypeError,json.JSONDecodeError):
            self.respond(400,{'error':'Invalid application data'})
        except Exception:
            self.respond(503,{'error':'History service is unavailable. Your local changes are retained.'})
