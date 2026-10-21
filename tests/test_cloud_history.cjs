const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'..','docs','app.js'),'utf8');
const code=source.slice(0,source.indexOf("window.addEventListener('focus'"));
let records={},offline=false,expired=false;
const clone=x=>JSON.parse(JSON.stringify(x));
function merge(remote,local){const result={...remote};for(const[key,entry]of Object.entries(local)){const current=result[key];if(!current||entry.updated_at>current.updated_at||(entry.updated_at===current.updated_at&&entry.mutation_id>current.mutation_id))result[key]=entry;}return result;}
function device(initial={}){
 const storage=new Map(Object.entries(initial)),session=new Map([['jobsearch.historySession.v1',JSON.stringify({token:'test-session',expires_at:Date.now()+3600000})]]),nodes={};let id=0;
 const ctx={console,Date,Object,JSON,Number,Set,Map,TextEncoder,TextDecoder,Uint8Array,btoa,atob,crypto:{randomUUID:()=>`mutation-${++id}`},setTimeout:()=>1,clearTimeout:()=>{},
  document:{getElementById:key=>nodes[key]||={textContent:'',value:''}},
  localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},sessionStorage:{getItem:key=>session.get(key)||null,setItem:(key,value)=>session.set(key,value),removeItem:key=>session.delete(key)},
  fetch:async(url,options={})=>{assert.strictEqual(url,'https://jobsearch-history.vercel.app/api/history');if(offline)throw new Error('Offline');if(expired)return {ok:false,status:401,json:async()=>({error:'Sign in'})};assert.strictEqual(options.headers.Authorization,'Bearer test-session');if(options.method==='POST')records=merge(records,JSON.parse(options.body).records);const snapshot=clone(records);return {ok:true,status:200,json:async()=>({version:1,records:snapshot})};}
 };vm.createContext(ctx);vm.runInContext(code,ctx);vm.runInContext('render=()=>{}',ctx);return {ctx,storage,session,nodes,run:s=>vm.runInContext(s,ctx)};
}
async function main(){
 const empty=device();empty.session.clear();empty.ctx.renderAppliedHistory();empty.ctx.renderMetrics();
 assert.strictEqual(empty.nodes['applied-count'].textContent,'not loaded');assert.strictEqual(empty.nodes['m-applied'].textContent,'—');
 empty.session.set('jobsearch.historySession.v1',JSON.stringify({token:'test-session',expires_at:Date.now()+3600000}));
 await empty.ctx.syncPrivateHistory();empty.ctx.renderAppliedHistory();empty.ctx.renderMetrics();
 assert.strictEqual(empty.nodes['applied-count'].textContent,0);assert.strictEqual(empty.nodes['m-applied'].textContent,0);

 const key='example:example:1',application={title:'Example role',applied_at:'2026-10-01T12:00:00Z'};
 const a=device({'jobsearch.status.v1':JSON.stringify({[key]:'applied'}),'jobsearch.applied.v1':JSON.stringify({[key]:application})});await a.ctx.syncPrivateHistory();assert.deepStrictEqual(records[key].application,application);
 const b=device();await b.ctx.syncPrivateHistory();assert.deepStrictEqual(JSON.parse(b.storage.get('jobsearch.applied.v1'))[key],application);
 b.run(`changeStatus('${key}','interviewing')`);await b.ctx.syncPrivateHistory();await a.ctx.syncPrivateHistory();assert.strictEqual(JSON.parse(a.storage.get('jobsearch.status.v1'))[key],'interviewing');
 offline=true;a.run(`changeStatus('${key}','rejected')`);await a.ctx.syncPrivateHistory();assert(a.nodes['history-sync-status'].textContent.includes('not saved'));offline=false;await a.ctx.syncPrivateHistory();assert.strictEqual(records[key].status,'rejected');
 a.run(`changeStatus('${key}','new')`);await a.ctx.syncPrivateHistory();await b.ctx.syncPrivateHistory();assert(records[key].deleted);assert(!JSON.parse(b.storage.get('jobsearch.applied.v1'))[key]);
 expired=true;await b.ctx.syncPrivateHistory();assert(!b.session.has('jobsearch.historySession.v1'));assert(b.nodes['history-sync-status'].textContent.includes('expired'));
 console.log('Database sync tests passed: migration, second-device restoration, status updates, offline retry, removal, session expiry.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
