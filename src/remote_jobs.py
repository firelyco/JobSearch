"""Explicit remote evidence for assessment intake, never proof of US work eligibility."""
import re

def is_remote(job):
    if isinstance(job.get('is_remote'), bool):
        return job['is_remote']
    mode = str(job.get('workplace_type') or '').lower()
    if mode:
        return mode == 'remote'
    location = str(job.get('resolved_locations') or job.get('location') or '')
    if re.search(r'\b(?:not\s+remote|non[- ]remote|hybrid|on[- ]?site)\b', location, re.I):
        return False
    return bool(re.search(r'\bremote\b', location, re.I)) or job.get('source') == 'remotive'
