"""Load the private Markdown assessment policy. Never fall back to another rubric."""
import hashlib
import os
from pathlib import Path

DEFAULT_FILE=Path(__file__).resolve().parent.parent/'config/fitment_skill.md'
VERDICTS={'strong':'strong','middle':'medium','pass':'not'}

def load_text():
    text=os.environ.get('FITMENT_SKILL_TEXT')
    if text is None:
        path=Path(os.environ.get('FITMENT_SKILL_FILE') or DEFAULT_FILE)
        if not path.is_file():raise RuntimeError('Private fitment skill is missing; assessment stopped.')
        text=path.read_text(encoding='utf-8')
    if 'THREE VERDICTS ONLY' not in text or not all(word in text for word in ('Strong','Middle','Pass')):
        raise RuntimeError('Fitment skill must define Strong, Middle and Pass.')
    return text

def attach(rules):
    result=dict(rules or {})
    result['_fitment_skill']=load_text()
    result['fitment_skill_hash']=hashlib.sha256(result['_fitment_skill'].encode()).hexdigest()
    result['evidence_required']=True
    result['fit_policy_version']='skill-three-v1'
    result['comp_floor_usd']=250000
    return result

def context(inventory,rules):
    # Old fit_rules classifications are not an alternative policy. The private
    # inventory remains evidence; the Markdown alone supplies assessment rules.
    return {'inventory':inventory,'fitment_skill':rules['_fitment_skill']}

def prompt(phase,rules):
    from src.evidence_fit import EXTRACT_PROMPT,REVIEW_PROMPT
    if not rules.get('_fitment_skill'):raise RuntimeError('No fitment skill loaded')
    return EXTRACT_PROMPT if phase=='extraction' else REVIEW_PROMPT
