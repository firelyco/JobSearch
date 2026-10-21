"""Private scorer transport. No candidate evidence is written to public files."""
import os
import requests
import time

URL='https://jobsearch-history.vercel.app/api/history'


def _send(method, **kwargs):
    # Both reads and writes are idempotent (writes upsert by job/input hash).
    for attempt in range(3):
        try:
            response=method(URL,**kwargs)
        except (requests.Timeout,requests.ConnectionError):
            if attempt==2: raise
        else:
            if response.status_code not in {429,500,502,503,504} or attempt==2:return response
        time.sleep(0.5*(attempt+1))

def save_private(payload, *, job_key=None):
    token=os.environ.get('HISTORY_SCORER_TOKEN')
    if not token:raise RuntimeError('Private evidence storage is not configured')
    response=_send(requests.post,headers={'Authorization':'Bearer '+token},json={
        'action':'assessment_write' if job_key else 'inventory_write','payload':payload,
        **({'job_key':job_key} if job_key else {})},timeout=40)
    if response.status_code!=200:raise RuntimeError(f'Private evidence storage HTTP {response.status_code}')


def load_private(job_key, *, input_hash=None):
    token=os.environ.get('HISTORY_SCORER_TOKEN')
    if not token:raise RuntimeError('Private evidence storage is not configured')
    response=_send(requests.get,headers={'Authorization':'Bearer '+token},params={'action':'assessment','job_key':job_key,**({'input_hash':input_hash} if input_hash else {})},timeout=40)
    if response.status_code==404:return None
    if response.status_code!=200:raise RuntimeError(f'Private assessment read HTTP {response.status_code}')
    return response.json()['assessment']
