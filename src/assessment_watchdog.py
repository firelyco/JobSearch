"""Assessment-only restart guard. Never collects jobs or resets attempt budgets."""
from datetime import datetime,timezone
import json,os,subprocess

def should_dispatch(state,active_runs,latest_run,now):
    if active_runs:return False
    if not state.get('enabled') or state.get('status')=='archived':return False
    # Avoid tight loops when credentials/publication/provider failures repeat.
    if latest_run and latest_run.get('conclusion') not in ('success',None):
        ended=latest_run.get('updatedAt')
        if ended and (now-datetime.fromisoformat(ended.replace('Z','+00:00'))).total_seconds()<1800:return False
    return True

def main():
    from src import fit_backfill as b
    repo=os.environ.get('GITHUB_REPOSITORY','firelyco/JobSearch')
    result=subprocess.run(['gh','run','list','--repo',repo,'--workflow','fit.yml','--limit','30','--json','status,conclusion,updatedAt'],check=True,capture_output=True,text=True)
    runs=json.loads(result.stdout)
    active=[r for r in runs if r['status'] in ('queued','in_progress','waiting','pending','requested')]
    state=b.fit.load_json(b.STATE_FILE,{})
    if not should_dispatch(state,active,runs[0] if runs else None,datetime.now(timezone.utc)):
        print('No restart: assessment worker active, cooldown, or queue disabled.');return
    if b.main(['--should-continue'])!=0:
        print('No restart: no unfinished eligible assessments or cache recovery.');return
    subprocess.run(['gh','workflow','run','fit.yml','--repo',repo,'--ref','main','-f','backfill=true'],check=True)
    print('Restarted unfinished assessments; paid budgets and completed results preserved.')

if __name__=='__main__':main()
