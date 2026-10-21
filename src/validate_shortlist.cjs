const fs=require('fs'),path=require('path'),vm=require('vm'),crypto=require('crypto'),cp=require('child_process');
const root=path.resolve(__dirname,'..');
const snapshot=j=>({company:j.company??null,description:j.description??null,location:j.location??null,title:j.title??null,updated_at:j.updated_at??null,url:j.url??null});
const hash=j=>crypto.createHash('sha256').update(JSON.stringify(snapshot(j))).digest('hex');
const key=j=>`${j.source}:${j.company}:${j.id}`;
function normalize(source){const ctx={};vm.createContext(ctx);const start=source.indexOf('function applyFitFreshness('),end=source.indexOf('function loadStatuses(',start);if(start<0)throw Error('Dashboard freshness policy is missing');vm.runInContext(source.slice(start,end),ctx);return ctx.applyFitFreshness;}
function audit(before,after,source,previousSource=source){
 const fresh=normalize(source),previousFresh=previousSource.includes('function applyFitFreshness(')?normalize(previousSource):(s,p,h)=>({...s,recommendation:s.policy_version===p.version&&s.context_hash===p.context_hash&&s.scorer_revision===p.scorer_revision&&s.job_snapshot_hash===h?s.recommendation:'review'}),jobs=new Map(after.jobs.map(j=>[key(j),j])),findings=[];let previous=0,current=0;
 function visible(s,j,policy){return s&&fresh({...s},policy,hash(j)).recommendation==='strong';}
 for(const j of after.jobs)if(visible(after.scores[key(j)],j,after.policy))current++;
 for(const old of before.jobs){const k=key(old),s=before.scores[k];if(!s || previousFresh({...s},before.policy,hash(old)).recommendation!=='strong')continue;previous++;
  const j=jobs.get(k),n=after.scores[k];if(j&&visible(n,j,after.policy))continue;
  let reason='',blocked=false;
  if(!j)reason='Removed from active source data; inspect source availability/closure.';
  else if(n?.salary_check?.status==='below_floor')reason='Published base maximum below threshold.';
  else if(n?.validated===true && n.scored_at>s.scored_at && !fresh({...n},after.policy,hash(j)).refresh_pending && ['medium','not'].includes(n.recommendation))reason='New completed assessment changed verdict: '+n.recommendation;
  else if(before.policy.fitment_skill_hash!==after.policy.fitment_skill_hash || before.policy.scorer_revision!==after.policy.scorer_revision)reason='Skill or scoring rules changed; reassessment required.';
  else if(hash(old)!==hash(j))reason='Employer job inputs changed; reassessment required.';
  else{reason='Previously Strong job disappeared without a completed replacement or changed job/rubric.';blocked=true;}
  findings.push({key:k,company:old.company,title:old.title,reason,blocked});
 }
 return {version:1,checked_at:new Date().toISOString(),previous_strong:previous,current_strong:current,unexplained_losses:findings.filter(f=>f.blocked).length,findings};
}
function main(){const baseline=process.argv.find(a=>a.startsWith('--baseline='))?.split('=')[1]||'origin/main';const read=name=>JSON.parse(fs.readFileSync(path.join(root,'docs',name+'.json'),'utf8'));const prior=name=>JSON.parse(cp.execFileSync('git',['show',`${baseline}:docs/${name}.json`],{cwd:root,encoding:'utf8',maxBuffer:20*1024*1024}));const source=fs.readFileSync(path.join(root,'docs/app.js'),'utf8');const result=audit({jobs:prior('jobs'),scores:prior('fit_scores'),policy:prior('fit_policy')},{jobs:read('jobs'),scores:read('fit_scores'),policy:read('fit_policy')},source,cp.execFileSync('git',['show',`${baseline}:docs/app.js`],{cwd:root,encoding:'utf8'}));result.baseline=cp.execFileSync('git',['rev-parse',baseline],{cwd:root,encoding:'utf8'}).trim();if(!process.argv.includes('--check-only'))fs.writeFileSync(path.resolve(root,process.argv.find(a=>a.startsWith('--report='))?.slice(9)||'docs/shortlist_validation.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));if(process.env.GITHUB_STEP_SUMMARY)fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,`\nShortlist validation: ${result.previous_strong} previous / ${result.current_strong} current or pending Strong; ${result.unexplained_losses} unexplained losses.\n`);if(result.unexplained_losses)process.exitCode=1;}
module.exports={audit,hash};if(require.main===module)main();
