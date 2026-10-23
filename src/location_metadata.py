"""Keep verified location display enrichment separate from assessed employer inputs."""
import re
from src.evidence_batch import snapshot_hash
from src.scorer import _is_us_or_remote

def enrich(job, previous, verdict, policy, resolved):
    keep=(previous and re.fullmatch(r"\d+\s+locations?",previous.get('location',''),re.I)
          and snapshot_hash(job)==snapshot_hash(previous)
          and verdict.get('validated') is True and verdict.get('assessment_status')=='complete'
          and verdict.get('location_status')=='eligible'
          and verdict.get('job_snapshot_hash')==snapshot_hash(previous)
          and all(policy.get(field) and verdict.get(field)==policy[field]
                  for field in ('context_hash','scorer_revision','fitment_skill_hash'))
          and verdict.get('policy_version')==policy.get('version')
          and _is_us_or_remote(re.sub(r'\bremote\b','',resolved,flags=re.I)))
    if keep:job['resolved_locations']=resolved
    else:job['location']=resolved
    return bool(keep)
