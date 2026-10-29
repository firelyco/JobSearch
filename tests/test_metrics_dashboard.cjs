const vm=require('vm'),fs=require('fs'),assert=require('assert');
const ctx={Intl,Date,Map,Set,URL};vm.createContext(ctx);vm.runInContext(fs.readFileSync('docs/metrics.js','utf8'),ctx);const m=ctx.JobMetrics;
assert.equal(m.day('2026-10-22'),'2026-10-22');assert.equal(m.range('weekly','').start,'');
assert.equal(m.day('2026-10-27T03:59:59Z'),'2026-10-26');assert.equal(m.day('2026-10-27T04:00:00Z'),'2026-10-27');
assert.equal(m.day('2026-11-01T05:30:00Z'),'2026-11-01');assert.equal(m.day('2026-11-01T06:30:00Z'),'2026-11-01');
assert.equal(JSON.stringify(m.range('weekly','2026-10-27')),JSON.stringify({start:'2026-10-26',end:'2026-11-01'}));
assert.equal(m.range('monthly','2026-02-18').end,'2026-02-28');assert.equal(m.range('monthly','2028-02-18').end,'2028-02-29');
const job={source:'greenhouse',company:'Lyft',id:'1',title:'Director TPM',url:'https://employer.example/1',location:'USA - Remote'};
const event=(id,type,at,data={})=>({id,type,at,job_key:'gh:lyft:1',job,...data});
const events=[event('d','discovered','2026-10-22T20:00:00Z'),event('s','screened','2026-10-22T20:00:01Z',{eligible:true,retained:true}),
event('a1','attempt_started','2026-10-22T20:01:00Z',{attempt_id:'a'}),event('f1','attempt_finished','2026-10-22T20:03:30Z',{attempt_id:'a',status:'failed',error:'APITimeoutError',seconds:150,calls:1,unknown_usage_calls:1}),
event('a2','attempt_started','2026-10-22T20:04:00Z',{attempt_id:'b'}),event('f2','attempt_finished','2026-10-22T20:06:30Z',{attempt_id:'b',status:'failed',error:'APITimeoutError',seconds:150,calls:1,unknown_usage_calls:1}),
event('q','queue_observed','2026-10-22T20:07:00Z',{status:'unresolved',attempt_count:2}),{id:'base',type:'history_baseline',at:'2026-10-21T12:00:00Z'}];
let r=m.analyze([...events,events[0]],{start:'2026-10-22',end:'2026-10-22'});
assert.equal(r.discovered.size,1);assert.equal(r.attempted.size,1);assert.equal(r.attempts,2);assert.equal(r.failedAttempts,2);assert.equal(r.completed.size,0);assert.equal(r.unresolved.size,1);assert.equal(r.median,150);assert.equal(r.unknownUsage,2);
assert.equal(r.states.get('gh:lyft:1').job.url,job.url);assert.equal(r.states.get('gh:lyft:1').error,'APITimeoutError');
const complete=event('c','completed','2026-10-27T15:00:00Z',{verdict:'strong',salary_status:'potentially_meets_floor'});
r=m.analyze([...events,complete],{start:'2026-10-27',end:'2026-10-27'});assert.equal(r.discovered.size,0);assert.equal(r.completed.size,1);assert.equal(r.verdictSets.strong.size,1);assert.equal(r.unresolved.size,0);
const reused=event('reuse','reused','2026-10-27T16:00:00Z');r=m.analyze([...events,complete,reused],{start:'2026-10-27',end:'2026-10-27'});assert.equal(r.reused.size,1);assert.equal(r.attempts,0);
assert.equal(m.analyze([...events,complete],{start:'2026-10-27',end:'2026-10-27'},{company:'Other'}).completed.size,0);
assert.equal(m.safeURL('javascript:alert(1)'),'');assert.equal(m.safeURL(job.url),job.url);
assert.equal(m.analyze(events,{start:'2026-10-01',end:'2026-10-01'}).partial,true);
const repeated={...complete,id:'c2',at:'2026-10-27T16:00:00Z'};r=m.analyze([...events,complete,repeated],{start:'2026-10-27',end:'2026-10-27'});assert.equal(r.completed.size,1);assert.equal(r.buckets[0].completed,1);
const priorStrong={...complete,id:'earlier',at:'2026-10-23T10:00:00Z'};r=m.analyze([...events,priorStrong,complete],{start:'2026-10-27',end:'2026-10-27'});assert.equal(r.newlyStrong.size,0);assert.equal(r.verdictChanged.size,0);const downgrade={...complete,id:'downgrade',verdict:'medium'};r=m.analyze([...events,priorStrong,downgrade],{start:'2026-10-27',end:'2026-10-27'});assert.equal(r.verdictChanged.size,1);
console.log('Metrics checks passed: NY/DST periods, deduplication, two timeouts/one job, backlog vs discovery, cache reuse, original links and missing history.');

// Cohort accounting must remain exclusive across retries, verdict changes and sources.
const mk=(id,key,type,at,extra={})=>({id,job_key:key,type,at,job:{source:'greenhouse',company:'Example',id:key,title:'Principal Technical Program Manager',url:'https://jobs.example.com/'+key,location:'US'},...extra});
const cohortEvents=[
 mk('old-d','old','discovered','2026-10-25T10:00:00Z'),mk('old-s','old','screened','2026-10-25T10:00:01Z',{eligible:true,retained:true}),
 mk('old-c','old','completed','2026-10-29T14:00:00Z',{verdict:'strong'}),
 mk('a-d','a','discovered','2026-10-29T12:00:00Z'),mk('a-s','a','screened','2026-10-29T12:01:00Z',{eligible:true,retained:true}),
 mk('a-c','a','completed','2026-10-29T12:02:00Z',{verdict:'strong'}),mk('a-c2','a','completed','2026-10-29T12:03:00Z',{verdict:'medium'}),
 mk('b-d','b','discovered','2026-10-29T12:00:00Z'),mk('b-s','b','screened','2026-10-29T12:01:00Z',{eligible:false,retained:false,reason:'Salary below minimum'}),
 mk('c-d','c','discovered','2026-10-29T12:00:00Z'),mk('c-s','c','screened','2026-10-29T12:01:00Z',{eligible:true,retained:true}),mk('c-a','c','attempt_started','2026-10-29T12:02:00Z'),
 mk('e-d','e','discovered','2026-10-29T12:00:00Z'),mk('e-s','e','screened','2026-10-29T12:01:00Z',{eligible:true,retained:true}),mk('e-b','e','blocked','2026-10-29T12:02:00Z',{reason:'AttemptBudgetExhausted'}),
 mk('w-d','w','discovered','2026-10-29T12:00:00Z'),mk('w-s','w','screened','2026-10-29T12:01:00Z',{eligible:true,retained:true}),
 mk('u-d','u','discovered','2026-10-29T12:00:00Z'),
 mk('back-d','back','discovered','2026-10-25T10:00:00Z'),mk('back-s','back','screened','2026-10-29T12:01:00Z',{eligible:true,retained:true}),
 {id:'baseline',type:'history_baseline',at:'2026-10-21T12:00:00Z'}
];
const period={start:'2026-10-29',end:'2026-10-29'};
let summary=m.overview(cohortEvents,period);
assert.equal(summary.total,6);assert.equal(summary.completed,1);assert.equal(summary.groups.medium.size,1);assert.equal(summary.groups.strong.size,0);
assert.equal(summary.groups.excluded.size,1);assert.equal(summary.groups.waiting.size,1);assert.equal(summary.groups.processing.size,1);assert.equal(summary.groups.blocked.size,2);
assert.equal(summary.eligible,5);assert.equal(summary.percent,20);assert.equal(summary.groups.backlogWaiting.size,1);
for(const rows of [summary.sourceRows,summary.roleRows])for(const key of ['discovered','completed','strong','medium','not','waiting','processing','blocked','excluded'])assert.equal(rows.reduce((n,row)=>n+row.groups[key].size,0),summary.groups[key].size);
assert.equal(summary.completed,summary.groups.strong.size+summary.groups.medium.size+summary.groups.not.size);
assert.equal(summary.total,summary.completed+summary.groups.waiting.size+summary.groups.processing.size+summary.groups.blocked.size+summary.groups.excluded.size);
// Multiple discovery events and source keys for one posting cannot create new jobs.
const alias={...mk('alias-d','alias','discovered','2026-10-29T14:00:00Z'),job:{...cohortEvents[0].job,url:cohortEvents[0].job.url+'?utm_source=email',source:'web_discovery'}};
summary=m.overview([...cohortEvents,alias],period);assert.equal(summary.total,6,'An older posting rediscovered elsewhere is not new');
const sameDayAlias={...mk('alias-a','aliasA','discovered','2026-10-29T14:00:00Z'),job:{...cohortEvents[3].job,url:cohortEvents[3].job.url+'?utm_source=email',source:'web_discovery'}};
summary=m.overview([...cohortEvents,sameDayAlias],period);assert.equal(summary.total,6);assert.equal(summary.completed,1);
const noJobs=m.overview(cohortEvents,{start:'2026-10-30',end:'2026-10-30'});assert.equal(noJobs.total,0);assert.equal(noJobs.percent,0);
assert.equal(m.overview(cohortEvents,period,{company:'Different'}).total,0);
assert.equal(m.roleCategory('Director, Enterprise PMO'),'PMO / Portfolio Leadership');assert.equal(m.roleCategory('Senior Manager, Technical Program Management'),'TPM Manager');assert.equal(m.roleCategory('Staff Technical Program Manager'),'Principal / Staff TPM');
assert.equal(m.sourceCategory({source:'ashby',discovery_sources:['a16z','sequoia']}),'a16z portfolio');
const duplicateParts=m.canonicalEvents([mk('q1','q1','discovered','2026-10-29T12:00:00Z',{job:{url:'https://jobs.example.com/view?jobId=1'}}),mk('q2','q2','discovered','2026-10-29T12:00:00Z',{job:{url:'https://jobs.example.com/view?jobId=2'}})]);
assert.notEqual(duplicateParts[0].job_key,duplicateParts[1].job_key,'Distinct requisition query IDs stay distinct');
console.log('Cohort checks passed: exclusive verdicts, backlog separation, pivot totals, progress, unknown eligibility, source deduplication, role groups and original requisition IDs.');

// A worker snapshot retires old waiting records and provides exact live counts.
const snapshot={id:'snapshot',type:'queue_snapshot',at:'2026-10-29T16:00:00Z',jobs:[{job_key:'a',status:'assessed',verdict:'medium'},{job_key:'c',status:'in_progress'},{job_key:'e',status:'unresolved'}]};
const authoritative=m.overview([...cohortEvents,snapshot],period);
assert.equal(authoritative.groups.waiting.size,0);assert.equal(authoritative.groups.backlogWaiting.size,0);
assert.equal(authoritative.groups.processing.size,1);assert.equal(authoritative.groups.blocked.size,1);
assert.equal(authoritative.groups.excluded.size,3);
assert.equal(authoritative.groups.completed.size,1);
