"""Bounded transport retry for public feeds. No AI inference or private data."""
import time
import requests

def get_json(url, **kwargs):
    for attempt in range(3):
        try:
            r=requests.get(url,timeout=20,**kwargs)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException,ValueError) as exc:
            status=getattr(getattr(exc,'response',None),'status_code',None)
            if attempt==2 or status is not None and status not in (408,429,500,502,503,504):raise
            time.sleep(15*(attempt+1) if status==429 else 0.5*(attempt+1))
