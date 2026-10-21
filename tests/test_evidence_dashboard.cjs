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
let backfill={};
const nodes={};
const ctx={Date,Object,console,URL,Uint8Array,TextEncoder,crypto:crypto.webcrypto,
 $:id=>nodes[id]??={textContent:'',innerHTML:''},
 fitScores:{},allJobs:[],metaData:{},tailoredJobs:{},jobsByKey:{},
 jobKey:j=>`${j.source}:${j.company}:${j.id}`,loadStatuses:()=>({}),loadInFlight:()=>({}),render:()=>{},syncPrivateHistory:()=>{},
 fetch:async url=>({ok:true,json:async()=>url.startsWith('fit_backfill.json')?backfill:url.startsWith('jobs.json')?jobs:url.startsWith('fit_scores.json')?structuredClone(scores):url.startsWith('fit_policy.json')?{version:'v1',evidence_required:true,context_hash:'profile1',scorer_revision:'code1'}:{}}),
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
 backfill={enabled:true,status:'running',phase:'windows',current_window:2,counts:{assessed:7,total:20,pending:10,in_progress:2,unresolved:1}};
 await ctx.loadData();
 assert(nodes['fit-backfill-status'].textContent.includes('7/20 assessed; 10 pending; 2 in progress; 1 unresolved'));
 assert(nodes['fit-backfill-status'].textContent.includes('window 3 of 14'));
 backfill.status='completed_with_issues';await ctx.loadData();
 assert(nodes['fit-backfill-status'].textContent.includes('unresolved roles still need review'));
 assert(!nodes['fit-backfill-status'].textContent.includes('All seven days assessed'));

 const html=fs.readFileSync(path.join(root,'docs/index.html'),'utf8');
 assert(html.includes('<option value="strong" selected>strong</option>'));
 assert(html.includes('<option value="us" selected>US</option>'));
 assert(!html.includes('TITLE MATCH'));
 assert(!html.includes('<option value="reach"'));
 assert(!html.includes('<option value="review"'));
 assert(!code.includes("label: 'Reach'"));
 assert(!code.includes("? 'Top tier'"));
 assert(html.includes('<option value="7" selected>posted: last 7 days</option>'));
 vm.runInContext(code.slice(code.indexOf('function baseSalaryStatus('),code.indexOf('function matchesWorkmode(')),ctx);
 const fresh={engine_version:'evidence-v1',comp_status:'meets_floor'};
 assert(ctx.matchesBaseSalary(fresh,'confirmed'));
 assert(ctx.matchesBaseSalary(undefined,'eligible'));
 assert(ctx.matchesBaseSalary({...fresh,comp_status:'potentially_meets_floor'},'eligible'));
 assert(!ctx.matchesBaseSalary({...fresh,comp_status:'below_floor'},'eligible'));
 assert(html.includes('<option value="eligible" selected>base max $250K+ / unverified</option>'));
 assert(!ctx.matchesBaseSalary({...fresh,refresh_pending:true},'confirmed'));
 assert(ctx.matchesBaseSalary({...fresh,refresh_pending:true},'unknown'));
 assert(ctx.matchesBaseSalary({...fresh,comp_status:'potentially_meets_floor'},'possible'));
 assert(!ctx.matchesBaseSalary({...fresh,comp_status:'potentially_meets_floor'},'confirmed'));
 assert(!ctx.matchesBaseSalary(undefined,'possible'));
 vm.runInContext(code.slice(code.indexOf('const ROLE_CATEGORIES ='),code.indexOf('let allJobs =')),ctx);
 assert(vm.runInContext("ROLE_CATEGORIES.find(r=>r.id==='principal').pattern.test('Staff Glasses Technical Program Manager, XR')",ctx));
 assert(vm.runInContext("!ROLE_CATEGORIES.find(r=>r.id==='principal').pattern.test('Staffing Technical Program Manager')",ctx));
 assert(vm.runInContext("ROLE_CATEGORIES.find(r=>r.id==='senior_ic').pattern.test('Senior Leadership Technical Program Manager, Product Development Ecosystem')",ctx));
 assert(vm.runInContext("!ROLE_CATEGORIES.find(r=>r.id==='senior_ic').pattern.test('Junior Technical Program Manager')",ctx));
 vm.runInContext(code.slice(code.indexOf('const LOCATION_MATCHERS ='),code.indexOf('// Salary eligibility')),ctx);
 const workday={source:'workday',url:'https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/TPM_JR123'};
 assert(ctx.matchesLocation('2 Locations','us',workday));
 assert(ctx.matchesLocation('','us',workday));
 assert(!ctx.matchesLocation('2 Locations','us'));
 assert(!ctx.matchesLocation('London, UK','us',workday));
 assert(!ctx.matchesLocation('Remote','us',workday));
 assert(!ctx.matchesLocation('2 Locations','canada',workday));
 assert(!ctx.matchesLocation('2 Locations','us',{...workday,source:'other'}));
 assert(!ctx.matchesLocation('2 Locations','us',{...workday,url:'https://example.com/job/US-CA-Santa-Clara/TPM'}));
 assert(!ctx.matchesLocation('2 Locations','us',{...workday,url:'https://nvidia.wd5.myworkdayjobs.com/job/CA-Toronto/TPM'}));
 assert(!ctx.matchesLocation('2 Locations','us',{...workday,url:'not a URL'}));
 assert(ctx.matchesLocation('2 Locations','us',{...workday,url:'https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One/job/McLean-VA/Director_R123'}));
 assert(ctx.matchesLocation('2 Locations','us',{...workday,url:'https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One/job/Richmond-VA/Director_R123'}));
 assert(!ctx.matchesLocation('2 Locations','us',{...workday,url:'https://example.wd5.myworkdayjobs.com/en-US/Careers/job/London-ON/TPM'}));
 assert(!ctx.matchesLocation('2 Locations','us',{...workday,url:'https://example.wd5.myworkdayjobs.com/en-US/Careers/job/Toronto-CA/TPM'}));
 assert(!ctx.matchesLocation('2 Locations','us',{...workday,url:'https://example.wd5.myworkdayjobs.com/en-US/Careers/job/Remote-CA/TPM'}));
 assert(ctx.matchesLocation('Boston, MA','us'));
 vm.runInContext(code.slice(code.indexOf('function baseSalaryLabel('),code.indexOf('function matchesWorkmode(')),ctx);
 assert.equal(ctx.baseSalaryLabel({...fresh,salary_check:{status:'meets_floor',bands:[{min:260000,max:300000}]}}),'$260,000?$300,000 USD base');
 assert.equal(ctx.baseSalaryLabel({...fresh,comp_status:'unknown',salary_check:{verification:'not_found_in_capture'}}),'Pay not found in captured posting');
 assert.equal(ctx.baseSalaryLabel({...fresh,comp_status:'unknown',salary_check:{verification:'base_and_variable_combined'}}),'Base / variable pay not separated');
 assert(ctx.baseSalaryLabel({...fresh,comp_status:'unknown',salary_check:{verification:'ambiguous_salary'}}).includes('verification'));
 const now=Date.parse('2026-10-16T18:00:00Z');
 assert(ctx.matchesPostingWindow({posted_at:'2026-10-15T12:00:00Z'},7,now));
 assert(!ctx.matchesPostingWindow({posted_at:'2026-06-01',first_seen_at:'2026-10-15'},7,now));
 assert(ctx.matchesPostingWindow({first_seen_at:'2026-10-15'},7,now));
 assert(!ctx.matchesPostingWindow({posted_at:'2026-10-17'},7,now));
 assert(!ctx.matchesPostingWindow({},7,now));
 console.log('Dashboard checks passed: current evidence stays Strong; stale job/profile/code/legacy ratings require review; Strong + US defaults retained.');
})().catch(error=>{console.error(error);process.exitCode=1;});
