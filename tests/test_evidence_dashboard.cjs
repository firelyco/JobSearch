const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
const path=require('path');
const root=process.env.JOBSEARCH_ROOT || path.join(__dirname,'..');
const code=fs.readFileSync(path.join(root,'docs/app.js'),'utf8');
const digest=value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const jobs=['current','legacy','job_changed','profile_changed','code_changed'].map(id=>({source:'test',company:'Example',id,title:'Principal TPM — AI',location:'Boston, MA',url:'https://example.com/'+id}));
const snapshot=job=>({company:job.company??null,description:job.description??null,location:job.location??null,title:job.title??null,updated_at:job.updated_at??null,url:job.url??null});
const scores=Object.fromEntries(jobs.map(job=>['test:Example:'+job.id,{recommendation:'strong',policy_version:'v1',engine_version:'evidence-v1',job_snapshot_hash:digest(snapshot(job)),context_hash:'profile1',scorer_revision:'code1'}]));
scores['test:Example:legacy'].policy_version='old';
scores['test:Example:job_changed'].job_snapshot_hash='outdated';
scores['test:Example:profile_changed'].context_hash='outdated';
scores['test:Example:code_changed'].scorer_revision='outdated';
jobs.push({source:'test',company:'Example',id:'unassessed',title:'New startup TPM',location:'Boston, MA'});
const nodes={};
const ctx={Date,Object,console,Uint8Array,TextEncoder,crypto:crypto.webcrypto,
 $:id=>nodes[id]??={textContent:'',innerHTML:''},
 fitScores:{},allJobs:[],metaData:{},tailoredJobs:{},jobsByKey:{},
 jobKey:j=>`${j.source}:${j.company}:${j.id}`,loadStatuses:()=>({}),loadInFlight:()=>({}),render:()=>{},syncPrivateHistory:()=>{},
 fetch:async url=>({ok:true,json:async()=>url.startsWith('jobs.json')?jobs:url.startsWith('fit_scores.json')?structuredClone(scores):url.startsWith('fit_policy.json')?{version:'v1',evidence_required:true,context_hash:'profile1',scorer_revision:'code1'}:{}}),
};
vm.createContext(ctx);
vm.runInContext(code.slice(code.indexOf('async function sha256Hex('),code.indexOf('function loadStatuses(')),ctx);
vm.runInContext(code.slice(code.indexOf('async function loadData()'),code.indexOf('async function refreshTailoredJobs()')),ctx);
(async()=>{
 await ctx.loadData();
 assert.equal(ctx.fitScores['test:Example:current'].recommendation,'strong');
 for(const id of ['legacy','job_changed','profile_changed','code_changed']){
  const rating=ctx.fitScores['test:Example:'+id];assert.equal(rating.recommendation,'review',id);assert.equal(rating.refresh_pending,true,id);
 }
 assert(nodes['fit-status'].textContent.includes('1 of 6 roles have current assessments; 5 await assessment or reassessment'));
 assert.equal(ctx.fitScores['test:Example:unassessed'],undefined);
 const html=fs.readFileSync(path.join(root,'docs/index.html'),'utf8');
 assert(html.includes('<option value="strong" selected>strong</option>'));
 assert(html.includes('<option value="us" selected>US</option>'));
 assert(!html.includes('TITLE MATCH'));
 assert(html.includes('<option value="7" selected>posted: last 7 days</option>'));
 vm.runInContext(code.slice(code.indexOf('function baseSalaryStatus('),code.indexOf('function matchesWorkmode(')),ctx);
 const fresh={engine_version:'evidence-v1',comp_status:'meets_floor'};
 assert(ctx.matchesBaseSalary(fresh,'confirmed'));
 assert(!ctx.matchesBaseSalary({...fresh,refresh_pending:true},'confirmed'));
 assert(ctx.matchesBaseSalary({...fresh,refresh_pending:true},'unknown'));
 assert(ctx.matchesBaseSalary({...fresh,comp_status:'potentially_meets_floor'},'possible'));
 assert(!ctx.matchesBaseSalary({...fresh,comp_status:'potentially_meets_floor'},'confirmed'));
 assert(!ctx.matchesBaseSalary(undefined,'possible'));
 const now=Date.parse('2026-10-16T18:00:00Z');
 assert(ctx.matchesPostingWindow({posted_at:'2026-10-15T12:00:00Z'},7,now));
 assert(!ctx.matchesPostingWindow({posted_at:'2026-06-01',first_seen_at:'2026-10-15'},7,now));
 assert(ctx.matchesPostingWindow({first_seen_at:'2026-10-15'},7,now));
 assert(!ctx.matchesPostingWindow({posted_at:'2026-10-17'},7,now));
 assert(!ctx.matchesPostingWindow({},7,now));
 console.log('Dashboard checks passed: current evidence stays Strong; stale job/profile/code/legacy ratings require review; Strong + US defaults retained.');
})().catch(error=>{console.error(error);process.exitCode=1;});
