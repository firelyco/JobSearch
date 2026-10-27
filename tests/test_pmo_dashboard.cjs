const fs=require('fs'),vm=require('vm'),assert=require('assert');
const code=fs.readFileSync('docs/app.js','utf8'),ctx={};vm.createContext(ctx);
vm.runInContext(code.slice(code.indexOf('const ROLE_CATEGORIES'),code.indexOf('let backfillIsActive'))+';globalThis.roles=ROLE_CATEGORIES',ctx);
const jobs=JSON.parse(fs.readFileSync('tests/fixtures/workiva_pmo_jobs.json','utf8'));
for(const job of jobs) {
  assert(ctx.roles.find(r=>r.id==='vp').pattern.test(job.title),job.title);
  assert(ctx.roles.some(r=>r.pattern?.test(job.title)||r.match?.(job)),job.title);
}
assert(ctx.roles.find(r=>r.id==='director').pattern.test('Director, PMO'));
assert(ctx.roles.find(r=>r.id==='manager').pattern.test('PMO Manager'));
console.log('PMO dashboard checks passed: Workiva VP roles and PMO Director/Manager remain selectable.');
