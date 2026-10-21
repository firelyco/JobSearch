"""Assessment scope preference; independent of cached career-fit validity."""
from datetime import date,datetime
import json
from pathlib import Path

CONFIG=Path(__file__).resolve().parent.parent/'config/assessment_queue.json'


def cutoff():
    config=json.loads(CONFIG.read_text(encoding='utf-8-sig')) if CONFIG.exists() else {'not_before':'2026-09-15'}
    return date.fromisoformat(config['not_before'])


def posting_date(job):
    for field in ('posted_at','first_seen_at'):
        try:return datetime.fromisoformat(str(job.get(field) or '').replace('Z','+00:00')).date()
        except (ValueError,TypeError):pass
    return None


def allowed(job):
    published=posting_date(job)
    # An undated record is not known to predate the cutoff. Normal polling adds first_seen_at.
    return published is None or published>=cutoff()
