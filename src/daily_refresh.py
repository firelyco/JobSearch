"""Recover a missed daily refresh without adding a schedule or repeating a successful day."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import json,os,subprocess
from pathlib import Path

TZ=ZoneInfo('America/New_York')
ROOT=Path(__file__).resolve().parents[1]
ACTIVE={'queued','in_progress','waiting','pending','requested'}

def local_day(value):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(TZ).date()
    except (ValueError,TypeError):return None

def needs_daily_refresh(meta,runs,now):
    today=now.astimezone(TZ)
    if today.hour<1:return False,'Daily refresh is not due before 1 AM Eastern.'
    if local_day(meta.get('polled_at'))==today.date():return False,'Today already has a published job refresh.'
    if any(r.get('status') in ACTIVE for r in runs):return False,'Job refresh is already queued or running.'
    latest=runs[0] if runs else None
    if latest and latest.get('conclusion') not in ('success',None):
        try:
            ended=datetime.fromisoformat(latest['updatedAt'].replace('Z','+00:00'))
            if (now-ended).total_seconds()<1800:return False,'Recent failed refresh is cooling down.'
        except (ValueError,KeyError):pass
    return True,'Daily refresh is due and no published refresh exists today.'

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--gate',action='store_true');args=parser.parse_args()
    try:meta=json.loads((ROOT/'docs/meta.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):meta={}
    now=datetime.now(timezone.utc)
    if args.gate:
        # Manual user runs remain available; scheduled/fallback runs are daily-only.
        daily=os.environ.get('GITHUB_EVENT_NAME')=='schedule' or os.environ.get('DAILY_ONLY')=='true'
        today=now.astimezone(TZ).date()
        run=not daily or local_day(meta.get('polled_at'))!=today
        if os.environ.get('RECOVERY_ONLY')=='true':
            try:batch=json.loads((ROOT/'docs/daily_batch.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):batch={}
            run=run or (batch.get('day')==today.isoformat() and batch.get('coverage_issues',0)>0)
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as out:out.write('run='+str(run).lower()+'\n')
        print('Daily refresh allowed.' if run else 'Skipped: today already has a published refresh.')
        return
    repo=os.environ.get('GITHUB_REPOSITORY','firelyco/JobSearch')
    result=subprocess.run(['gh','run','list','--repo',repo,'--workflow','poll.yml','--limit','20','--json','status,conclusion,updatedAt'],check=True,capture_output=True,text=True)
    due,reason=needs_daily_refresh(meta,json.loads(result.stdout),now);print(reason)
    if due:
        subprocess.run(['gh','workflow','run','poll.yml','--repo',repo,'--ref','main','-f','daily_only=true'],check=True)
        print('Started missed daily refresh; assessment processing follows the published poll.')

if __name__=='__main__':main()
