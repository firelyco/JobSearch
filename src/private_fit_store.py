"""Private scorer transport. No candidate evidence is written to public files."""
import os
import requests

URL='https://jobsearch-history.vercel.app/api/history'

def save_private(payload, *, job_key=None):
    token=os.environ.get('HISTORY_SCORER_TOKEN')
    if not token:raise RuntimeError('Private evidence storage is not configured')
    response=requests.post(URL,headers={'Authorization':'Bearer '+token},json={
        'action':'assessment_write' if job_key else 'inventory_write','payload':payload,
        **({'job_key':job_key} if job_key else {})},timeout=40)
    if response.status_code!=200:raise RuntimeError(f'Private evidence storage HTTP {response.status_code}')
