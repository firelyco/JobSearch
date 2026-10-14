const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(require('path').join(__dirname,'..','docs','app.js'),'utf8');
const code = source.slice(0,source.indexOf("window.addEventListener('focus'"));
const clone = x => JSON.parse(JSON.stringify(x));
let remote = {version:1,records:{}}, sha = 1, privateRepo = true, offline = false, conflict = false, writes = 0;
function device(initial={}) {
  const storage = new Map(Object.entries(initial));
  storage.set('jobsearch.pat.v1','github_pat_test');
  const nodes = {};
  let id=0;
  const ctx = {console,Date,Object,JSON,Set,Map,Number,TextEncoder,TextDecoder,Uint8Array,btoa,atob,
    crypto:{randomUUID:()=>`mutation-${++id}`},setTimeout:()=>1,clearTimeout:()=>{},
    document:{getElementById:key=>nodes[key] ||= {textContent:'',value:'',querySelectorAll:()=>[]}},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    fetch:async(url,options={})=>{
      if(offline) throw new Error('Offline');
      assert.strictEqual(options.headers.Authorization,'Bearer github_pat_test');
      if (!url.includes('/contents/')) return {ok:true,json:async()=>({private:privateRepo})};
      if(options.method==='PUT') {
        writes++;
        const body=JSON.parse(options.body);
        if(conflict) {conflict=false; remote.records['other-device']={status:'applied',application:{title:'Concurrent role'},updated_at:1,mutation_id:'other'};sha++;return {ok:false,status:409};}
        if(body.sha!==String(sha)) return {ok:false,status:409};
        remote=JSON.parse(Buffer.from(body.content,'base64').toString('utf8'));sha++;
        return {ok:true,status:200};
      }
      const snapshot=clone(remote), version=sha;
      return {ok:true,status:200,json:async()=>({sha:String(version),content:Buffer.from(JSON.stringify(snapshot)).toString('base64')})};
    }
  };
  vm.createContext(ctx);vm.runInContext(code,ctx);vm.runInContext('render=()=>{}',ctx);
  return {ctx,storage,nodes,run:s=>vm.runInContext(s,ctx)};
}
async function main() {
  const key='apple:apple:123';
  const original={title:'Apple TPM role',company:'apple',applied_at:'2026-10-01T12:00:00Z'};
  const a=device({'jobsearch.status.v1':JSON.stringify({[key]:'applied'}),'jobsearch.applied.v1':JSON.stringify({[key]:original})});
  await a.ctx.syncPrivateHistory();
  assert.deepStrictEqual(remote.records[key].application,original,'legacy migration preserves application date');
  const b=device();await b.ctx.syncPrivateHistory();
  assert.deepStrictEqual(JSON.parse(b.storage.get('jobsearch.applied.v1'))[key],original,'new device restores history');
  b.run(`changeStatus('${key}','interviewing')`);await b.ctx.syncPrivateHistory();await a.ctx.syncPrivateHistory();
  assert.strictEqual(JSON.parse(a.storage.get('jobsearch.status.v1'))[key],'interviewing');
  offline=true;a.run(`changeStatus('${key}','rejected')`);await a.ctx.syncPrivateHistory();
  assert(a.nodes['history-sync-status'].textContent.includes('not synced'));
  assert.strictEqual(JSON.parse(a.storage.get('jobsearch.status.v1'))[key],'rejected');
  offline=false;conflict=true;await a.ctx.syncPrivateHistory();
  assert(remote.records['other-device'],'conflict retry preserves concurrent record');
  assert.strictEqual(remote.records[key].status,'rejected');
  a.run(`changeStatus('${key}','new')`);await a.ctx.syncPrivateHistory();await b.ctx.syncPrivateHistory();
  assert(remote.records[key].deleted,'removal is a durable tombstone');
  assert(!JSON.parse(b.storage.get('jobsearch.applied.v1'))[key],'stale device cannot resurrect removed record');
  const before=writes;privateRepo=false;await b.ctx.syncPrivateHistory();
  assert.strictEqual(writes,before,'never writes history to public repository');
  assert(b.nodes['history-sync-status'].textContent.includes('must be private'));
  console.log('Private sync tests passed: migration, cross-device restore/statuses, offline retry, concurrent updates, removals, privacy guard.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
