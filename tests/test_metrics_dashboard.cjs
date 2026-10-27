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
console.log('Metrics checks passed: NY/DST periods, deduplication, two timeouts/one job, backlog vs discovery, cache reuse, original links and missing history.');
