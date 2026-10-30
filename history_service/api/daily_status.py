"""Public aggregate daily health. Never exposes credentials or career evidence."""
import json,os
from datetime import datetime,timezone,timedelta
from http.server import BaseHTTPRequestHandler
try:
    from lib.daily_logic import TZ
    from api.daily import SCHEMA
    from api.history import connect
except ImportError:
    from history_service.lib.daily_logic import TZ
    from history_service.api.daily import SCHEMA
    from history_service.api.history import connect

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        day=datetime.now(timezone.utc).astimezone(TZ).date().isoformat()
        try:
            if not os.environ.get('JOBSEARCH_GITHUB_TOKEN'):raise RuntimeError('Independent dispatch credential is not configured')
            with connect() as db:
                db.execute(SCHEMA);row=db.execute('SELECT payload FROM daily_pipeline_runs WHERE day=%s',(day,)).fetchone()
            body=row[0] if row else {'day':day,'status':'not_checked','reason':'Independent daily check has not run yet.'}
            checked=body.get('checked_at')
            if checked and body.get('status') not in ('complete','completed_late'):
                last=datetime.fromisoformat(checked.replace('Z','+00:00'))
                if datetime.now(timezone.utc)-last>timedelta(minutes=20):
                    body={**body,'status':'monitor_overdue','reason':'Independent progress check is overdue; daily completion is not verified.'}
            allowed={'day','deadline_at','checked_at','status','reason','counts','work_status','batch_id','completed_at'}
            payload=json.dumps({k:v for k,v in body.items() if k in allowed}).encode();code=200
        except Exception:payload=json.dumps({'day':day,'status':'configuration_required' if not os.environ.get('JOBSEARCH_GITHUB_TOKEN') else 'unavailable','reason':'Independent scheduler needs its GitHub dispatch credential.' if not os.environ.get('JOBSEARCH_GITHUB_TOKEN') else 'Daily progress could not be verified.'}).encode();code=200 if not os.environ.get('JOBSEARCH_GITHUB_TOKEN') else 503
        self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Access-Control-Allow-Origin','https://firelyco.github.io');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
