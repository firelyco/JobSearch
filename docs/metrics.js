/* Operational history is independent of the Jobs tab's shortlist filters. */
(function(root){
  'use strict';
  const TZ='America/New_York',labels={strong:'Strong',medium:'Middle',not:'Pass'};
  const dayFormat=new Intl.DateTimeFormat('en-US',{timeZone:TZ,year:'numeric',month:'2-digit',day:'2-digit'});
  function day(value){if(typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value))return value;const d=new Date(value);if(!Number.isFinite(+d))return '';const p=Object.fromEntries(dayFormat.formatToParts(d).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}`;}
  function shift(value,n){const d=new Date(value+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10);}
  function range(mode,anchor,end){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(anchor||''))return {start:'',end:''};
    if(mode==='custom')return {start:anchor,end:end||anchor};
    if(mode==='weekly'){const n=new Date(anchor+'T12:00:00Z').getUTCDay();const start=shift(anchor,-((n+6)%7));return {start,end:shift(start,6)};}
    if(mode==='monthly'){const start=anchor.slice(0,7)+'-01',d=new Date(start+'T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+1);return {start,end:shift(d.toISOString().slice(0,10),-1)};}
    return {start:anchor,end:anchor};
  }
  function unique(events){return [...new Map(events.map(e=>[e.id,e])).values()].sort((a,b)=>a.at.localeCompare(b.at)||a.id.localeCompare(b.id));}
  function safeURL(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:'';}catch{return '';}}
  function matches(job,filter){
    if(filter.company&&!String(job.company||'').toLowerCase().includes(filter.company.toLowerCase()))return false;
    if(filter.role&&!String(job.title||'').toLowerCase().includes(filter.role.toLowerCase()))return false;
    if(filter.source&&job.source!==filter.source)return false;
    if(filter.location==='us'&&!/\b(?:u\.?s\.?a?|united states)\b|,\s*(?:CA|NY|MA|WA|TX)\b/i.test(job.location||''))return false;
    if(filter.location==='remote'&&!(/\bremote\b/i.test(job.location||'')||job.is_remote===true))return false;
    return true;
  }
  function analyze(input,period,filter={}){
    const events=unique(input),states=new Map(),timeline=new Map();
    for(const e of events){
      if(!e.job_key||day(e.at)>period.end)continue;
      const s=states.get(e.job_key)||{key:e.job_key,job:{},status:'waiting',verdict:'',salary:'unknown',eligible:null,reason:'',attempt_count:null};
      s.job={...s.job,...e.job};s.latest=e.at;
      if(e.type==='discovered'&&!s.discovered)s.discovered=e.at;
      if(['screened','eligibility_observed'].includes(e.type)){s.eligible=e.eligible;s.retained=e.retained;s.screening_reason=e.reason;if(!e.retained)s.status='excluded';else if(s.status==='excluded')s.status=s.verdict?'completed':'waiting';}
      if(e.type==='attempt_started'){s.status='processing';s.last_attempt=e.at;}
      if(e.type==='attempt_finished'){
        s.status=e.status==='complete'?'completed':e.status==='failed'?'failed':'waiting';
        if(e.verdict&&labels[e.verdict])s.verdict=e.verdict;
        if(e.error)s.error=e.error;s.last_attempt=e.at;
      }
      if(e.type==='completed'){s.status='completed';s.verdict=e.verdict;s.salary=e.salary_status||'unknown';s.completed=e.at;}
      if(e.type==='queue_observed'){s.in_queue=e.in_queue;s.status=e.status;s.attempt_count=e.attempt_count;s.last_attempt=e.last_attempt_at||s.last_attempt;s.reason=e.reason||s.reason;s.recovered=e.provenance==='recovered';}
      if(e.type==='blocked'){if(s.status!=='completed')s.status='waiting';s.error=e.reason;}
      states.set(e.job_key,s);if(!timeline.has(e.job_key))timeline.set(e.job_key,[]);timeline.get(e.job_key).push(e);
    }
    const allowed=new Set([...states.values()].filter(s=>matches(s.job,filter)).map(s=>s.key));
    const within=e=>day(e.at)>=period.start&&day(e.at)<=period.end;
    const activity=events.filter(e=>within(e)&&(!e.job_key||allowed.has(e.job_key)));
    const set=type=>new Set(activity.filter(e=>e.type===type).map(e=>e.job_key));
    const discovered=set('discovered'),completed=set('completed');
    const starts=activity.filter(e=>e.type==='attempt_started'),finished=activity.filter(e=>e.type==='attempt_finished');
    const attempted=new Set([...starts,...finished].map(e=>e.job_key));
    const failed=finished.filter(e=>e.status==='failed'),failedJobs=new Set(failed.map(e=>e.job_key));
    const activityKeys=new Set(activity.filter(e=>['attempt_started','attempt_finished','completed','blocked','reused','queue_observed'].includes(e.type)).map(e=>e.job_key));
    const verdictSets={};for(const rec of Object.keys(labels))verdictSets[rec]=new Set(activity.filter(e=>e.type==='completed'&&e.verdict===rec).map(e=>e.job_key));
    const newlyStrong=new Set(),verdictChanged=new Set(),previousVerdict=new Map(),everStrong=new Set();
    for(const e of events){
      if(e.type!=='completed'||day(e.at)>period.end)continue;
      if(within(e)&&allowed.has(e.job_key)){
        if(e.verdict==='strong'&&!everStrong.has(e.job_key))newlyStrong.add(e.job_key);
        if(previousVerdict.has(e.job_key)&&previousVerdict.get(e.job_key)!==e.verdict)verdictChanged.add(e.job_key);
      }
      previousVerdict.set(e.job_key,e.verdict);if(e.verdict==='strong')everStrong.add(e.job_key);
    }
    const unresolved=new Set([...states.values()].filter(s=>allowed.has(s.key)&&s.status==='unresolved'&&s.in_queue!==false).map(s=>s.key));
    const eligible=new Set([...discovered].filter(k=>states.get(k)?.eligible===true));
    const excluded=new Set(activity.filter(e=>['screened','eligibility_observed'].includes(e.type)&&!e.eligible).map(e=>e.job_key));
    const waiting=new Set([...states.values()].filter(s=>allowed.has(s.key)&&s.status==='waiting'&&(s.eligible===true||s.in_queue===true)).map(s=>s.key));
    const processing=new Set([...states.values()].filter(s=>allowed.has(s.key)&&s.status==='processing'&&s.in_queue!==false).map(s=>s.key));
    const timeouts=finished.filter(e=>/Timeout/i.test(e.error||'')).length;
    const durations=finished.map(e=>e.seconds).filter(Number.isFinite).sort((a,b)=>a-b);
    const refreshes=activity.filter(e=>e.type==='refresh');
    const calls=finished.reduce((n,e)=>n+(e.calls||0),0),unknownUsage=finished.reduce((n,e)=>n+(e.unknown_usage_calls||0),0);
    const tokens=finished.reduce((n,e)=>n+(e.input_tokens||0)+(e.output_tokens||0),0);
    const reused=new Set(activity.filter(e=>e.type==='reused').map(e=>e.job_key));
    const baseline=events.find(e=>e.type==='history_baseline')?.at;
    const partial=!baseline||period.start<=day(baseline);
    const buckets=[];for(let date=period.start;date<=period.end;date=shift(date,1)){
      buckets.push({date,discovered:activity.filter(e=>e.type==='discovered'&&day(e.at)===date).length,completed:new Set(activity.filter(e=>e.type==='completed'&&day(e.at)===date).map(e=>e.job_key)).size});
      if(buckets.length>=370)break;
    }
    return {states,timeline,activity,newlyStrong,verdictChanged,waiting,processing,timeouts,discovered,completed,attempted,failedJobs,unresolved,eligible,excluded,activityKeys,verdictSets,refreshes,failedAttempts:failed.length,
      reused,attempts:new Set([...starts,...finished].map(e=>e.attempt_id)).size,calls,tokens,unknownUsage,partial,
      average:durations.length?durations.reduce((a,b)=>a+b,0)/durations.length:null,
      median:durations.length?(durations[Math.floor((durations.length-1)/2)]+durations[Math.ceil((durations.length-1)/2)])/2:null,
      buckets,recordedFrom:baseline};
  }
  const API={day,range,shift,unique,safeURL,analyze};root.JobMetrics=API;
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id),esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let history=[],loaded=false,loading=false,loadFailed=false,activeMetric='',page=0,current=null;
  const PAGE_SIZE=50;
  function selectedRange(){return range($('metrics-period').value,$('metrics-date').value,$('metrics-end').value);}
  function stamp(value){return value?new Intl.DateTimeFormat('en-US',{timeZone:TZ,dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'Not recorded';}
  const statusLabel=s=>({completed:'Completed',failed:'Assessment failed',waiting:'Waiting / not assessed',processing:'Processing',excluded:'Excluded during screening',unresolved:'Retry limit reached'}[s]||s);
  function salaryLabel(value){return {below_floor:'Published base max below $250K',meets_floor:'Base clears $250K',potentially_meets_floor:'Base range reaches $250K',unknown:'Base pay unverified'}[value]||'Base pay unverified';}
  function render(){
    if(!loaded)return;
    const period=selectedRange();
    if(!period.start||!period.end||period.end<period.start){$('metrics-status').textContent='Choose a valid start and end date.';return;}
    if((new Date(period.end)-new Date(period.start))/86400000>366){$('metrics-status').textContent='Choose a period of 366 days or less.';return;}
    const filters={company:$('metrics-company').value.trim(),role:$('metrics-role').value.trim(),source:$('metrics-source').value,location:$('metrics-location').value};
    const r=analyze(history,period,filters);current=r;
    $('metrics-range-label').textContent=`${period.start} through ${period.end} · America/New_York · ${history.length.toLocaleString()} saved events`;
    $('metrics-status').textContent=loadFailed?'Some history files could not be loaded. Counts are incomplete. Retry loading.':r.partial?'Recovered history is partial. Saved discovery and completion dates are included; earlier attempt, screening, source and usage details may not have been recorded. Missing history is not zero.':'Detailed events are recorded for this period. Job filters affect job counts; source checks describe the entire refresh.';
    const activityMode=$('metrics-view').value==='activity';
    const cards=[['discovered','New jobs discovered',r.discovered.size],['eligible','Eligible in discovery cohort',[...r.discovered].some(k=>r.states.get(k)?.eligible!==null)?r.eligible.size:'Not recorded'],['attempted','Unique jobs attempted',r.partial&&!r.attempts?'Not recorded':r.attempted.size],['completed','Unique jobs completed',r.completed.size],['reused','Saved reviews reused',r.reused.size],['strong','Strong results',r.verdictSets.strong.size],['newlyStrong','First Strong in recorded history',r.newlyStrong.size],['verdictChanged','Jobs with verdict changes',r.verdictChanged.size],['medium','Middle results',r.verdictSets.medium.size],['not','Pass results',r.verdictSets.not.size],['failedJobs','Failed attempts',r.partial&&!r.attempts?'Not recorded':r.failedAttempts],['unresolved','Unresolved active queue at period end',r.unresolved.size],['waiting','Waiting eligible jobs',r.waiting.size],['processing','Processing',r.processing.size],['excluded','Outside scope / excluded observations',r.activity.some(e=>['screened','eligibility_observed'].includes(e.type))?r.excluded.size:'Not recorded']];
    $('metrics-cards').innerHTML=cards.map(([id,label,value])=>`<button type="button" class="metrics-stat ${activeMetric===id?'selected':''}" data-metric="${id}" aria-pressed="${activeMetric===id}"><span>${label}</span><strong>${esc(typeof value==='number'?value.toLocaleString():value)}</strong><small>View jobs</small></button>`).join('');
    $('metrics-cards').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{activeMetric=activeMetric===b.dataset.metric?'':b.dataset.metric;page=0;render();}));
    const sourceChecks=r.refreshes.flatMap(e=>e.sources||[]);
    const failedSources=sourceChecks.filter(s=>s.status==='failed').length;
    const partialSources=sourceChecks.filter(s=>['partial','limit_reached','deferred','not_configured'].includes(s.status)).length;
    const oldestWaiting=[...r.waiting].map(k=>r.states.get(k)?.discovered).filter(Boolean).sort()[0];
    const wholeRefresh=r.refreshes.length?`${r.refreshes.length} refresh runs; ${r.refreshes.reduce((n,e)=>n+(e.raw_fetched||0),0).toLocaleString()} source postings fetched across those runs (includes repeated postings).`:'Source refresh history not recorded for this period.';
    const speed=r.average===null?'Timing not recorded.':`Average recorded attempt ${Math.round(r.average)}s; median ${Math.round(r.median)}s.`;
    $('metrics-health').textContent=`${wholeRefresh} ${sourceChecks.length} recorded source/query checks: ${failedSources} failed, ${partialSources} incomplete or deferred. ${speed} ${r.attempts} recorded attempts across ${r.attempted.size} jobs. ${r.reused.size} saved reviews reused; ${r.calls} recorded model calls; ${r.timeouts} timeouts among ${r.activity.filter(e=>e.type==='attempt_finished').length} finished attempts${r.activity.some(e=>e.type==='attempt_finished')?' ('+Math.round(100*r.timeouts/r.activity.filter(e=>e.type==='attempt_finished').length)+'%)':''}; ${r.tokens.toLocaleString()} reported tokens${r.unknownUsage?`; usage unavailable for ${r.unknownUsage} failed calls`:''}. ${oldestWaiting?'Oldest waiting job first discovered '+day(oldestWaiting)+'. ':''}Cost not available from billing data.`;
    const discoveryStates=[...r.discovered].map(k=>r.states.get(k));
    const funnel={discovered:r.discovered.size,eligible:discoveryStates.filter(s=>s.eligible===true).length,unknown:discoveryStates.filter(s=>s.eligible===null).length,excluded:discoveryStates.filter(s=>s.eligible===false).length,completed:discoveryStates.filter(s=>s.status==='completed').length,unfinished:discoveryStates.filter(s=>s.status!=='completed'&&s.eligible!==false).length};
    $('metrics-funnel').textContent=`Discovery cohort: ${funnel.discovered} found → ${funnel.eligible} eligible / ${funnel.excluded} excluded / ${funnel.unknown} eligibility not recorded. ${funnel.completed} have completed assessments by period end; ${funnel.unfinished} remain unfinished or unverified. Assessment activity below may include older backlog.`;
    const max=Math.max(1,...r.buckets.flatMap(b=>[b.discovered,b.completed]));
    $('metrics-trend').innerHTML=r.buckets.map(b=>`<tr><th scope="row">${b.date}</th><td><span class="metrics-bar" style="width:${100*b.discovered/max}%"></span>${b.discovered}</td><td><span class="metrics-bar completed" style="width:${100*b.completed/max}%"></span>${b.completed}</td></tr>`).join('');
    let keys=activeMetric?(r.verdictSets[activeMetric]||r[activeMetric]||new Set()):(activityMode?r.activityKeys:r.discovered);
    if(!(keys instanceof Set))keys=new Set();
    const rows=[...keys].map(k=>r.states.get(k)).filter(Boolean).sort((a,b)=>b.latest.localeCompare(a.latest));
    const totalPages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));page=Math.min(page,totalPages-1);
    $('metrics-jobs-label').textContent=`${activeMetric?cards.find(c=>c[0]===activeMetric)?.[1]:activityMode?'Assessment activity':'Discovery cohort'} · ${rows.length} jobs · page ${page+1} of ${totalPages}`;
    $('metrics-prev-page').disabled=page===0;$('metrics-next-page').disabled=page>=totalPages-1;
    $('metrics-jobs').innerHTML=rows.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE).map(s=>{
      const link=safeURL(s.job.url),reason=[s.error,s.reason,s.status==='excluded'||s.eligible===false?s.screening_reason:''].filter(Boolean).join(' · ');
      const events=(r.timeline.get(s.key)||[]).slice().reverse();
      const timeline=events.map(e=>`<li><time>${esc(stamp(e.at))}</time> — ${esc(e.type.replaceAll('_',' '))}${e.verdict?' · '+esc(labels[e.verdict]||e.verdict):''}${e.error?' · '+esc(e.error):''}${e.reason?' · '+esc(e.reason):''}${e.provenance==='recovered'?' (recovered)':''}${e.run_url?` · <a href="${esc(safeURL(e.run_url))}" target="_blank" rel="noopener">Run</a>`:''}</li>`).join('');
      return `<tr data-metrics-key="${esc(s.key)}"><td>${link?`<a href="${esc(link)}" target="_blank" rel="noopener">${esc(s.job.title||'Open posting')}</a>`:esc(s.job.title||'Posting details not recorded')}<div class="muted">${esc(s.job.company||s.key.split(':')[1])} · ${esc(s.job.source||s.key.split(':')[0])}</div><div class="muted">${esc(s.job.location||'Location not recorded')}</div></td><td>${esc(s.job.posted_at?day(s.job.posted_at):'Not recorded')}<br><small>Found ${esc(s.discovered?day(s.discovered):'not recorded')}</small></td><td>${esc(statusLabel(s.status))}<div class="muted">${esc(reason)}</div><small>${s.attempt_count===null?'':`${s.attempt_count} attempts observed; last ${esc(stamp(s.last_attempt))}`}</small></td><td>${esc(labels[s.verdict]||'No completed verdict')}<div class="muted">${esc(salaryLabel(s.salary))}</div></td><td><details><summary>History (${events.length})</summary><ol class="metrics-timeline">${timeline}</ol>${s.recovered?'<p>Older attempt counts were recovered; individual attempt timestamps and error details may be unavailable.</p>':''}</details></td></tr>`;
    }).join('')||'<tr><td colspan="5" class="empty">No recorded jobs in this view. Missing historical records do not prove that no work occurred.</td></tr>';
    $('metrics-sources').innerHTML=r.refreshes.slice().reverse().map(e=>`<details><summary>${esc(stamp(e.at))}: ${(e.raw_fetched||0).toLocaleString()} fetched, ${(e.retained||0).toLocaleString()} retained, ${e.new_reported??'unknown'} new, ${Number.isFinite(e.new_reported)&&Number.isFinite(e.retained)?Math.max(0,e.retained-e.new_reported):'unknown'} known retained${e.provenance==='recovered'?' (recovered)':''}</summary><div class="metrics-table-scroll"><table><thead><tr><th>Source / query</th><th>Status</th><th>Collected / reported</th></tr></thead><tbody>${(e.sources||[]).map(s=>`<tr><td>${esc(s.label||s.query||s.source)}</td><td>${esc(s.status)}</td><td>${esc(s.collected??s.jobs??'Not recorded')} / ${esc(s.reported_total??'Not recorded')}</td></tr>`).join('')}</tbody></table></div></details>`).join('')||'<p class="muted">No source run details were recorded for this period.</p>';
  }
  async function load(){
    if(loading)return;loading=true;$('metrics-status').textContent='Loading saved history…';
    try{
      const response=await fetch('metrics/index.txt?t='+Date.now());if(!response.ok)throw Error('History index is unavailable');
      const paths=[...new Set((await response.text()).trim().split(/\r?\n/))].filter(p=>/^\d{4}-\d{2}\/[a-zA-Z0-9_-]+\.json$/.test(p));
      const next=[];let failures=0,cursor=0;
      await Promise.all(Array.from({length:Math.min(6,paths.length)},async()=>{while(cursor<paths.length){const p=paths[cursor++];try{const r=await fetch('metrics/'+p+'?t='+Date.now());if(!r.ok)throw Error();const d=await r.json();if(!Array.isArray(d.events))throw Error();next.push(...d.events);}catch{failures++;}}}));
      history=unique(next);loaded=true;loadFailed=failures>0;
      const previous=$('metrics-source').value,sources=[...new Set(history.map(e=>e.job?.source).filter(Boolean))].sort();
      $('metrics-source').innerHTML='<option value="">All sources</option>'+sources.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');$('metrics-source').value=previous;
      $('metrics-updated').textContent='History loaded '+stamp(new Date().toISOString());render();
    }catch(error){$('metrics-status').textContent='Could not load saved metrics. Use Refresh history to retry; the Jobs tab remains available.';}
    finally{loading=false;}
  }
  function init(){
    $('metrics-tab').tabIndex=-1;const today=day(new Date());$('metrics-date').value=today;$('metrics-end').value=today;
    function show(metrics){$('jobs-view').classList.toggle('hidden',metrics);$('metrics-view-panel').classList.toggle('hidden',!metrics);$('jobs-tab').setAttribute('aria-selected',String(!metrics));$('jobs-tab').tabIndex=metrics?-1:0;$('metrics-tab').tabIndex=metrics?0:-1;$('metrics-tab').setAttribute('aria-selected',String(metrics));if(metrics&&!loaded)load();}
    $('jobs-tab').addEventListener('click',()=>show(false));$('metrics-tab').addEventListener('click',()=>show(true));
    document.querySelector('.dashboard-tabs').addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const metrics=$('metrics-tab').getAttribute('aria-selected')!=='true';show(metrics);$(metrics?'metrics-tab':'jobs-tab').focus();}});
    $('metrics-refresh').addEventListener('click',load);
    ['metrics-period','metrics-date','metrics-end','metrics-view','metrics-company','metrics-role','metrics-source','metrics-location'].forEach(id=>$(id).addEventListener('input',()=>{page=0;activeMetric='';$('metrics-end-label').classList.toggle('hidden',$('metrics-period').value!=='custom');render();}));
    for(const [id,direction] of [['metrics-previous',-1],['metrics-next',1]])$(id).addEventListener('click',()=>{const mode=$('metrics-period').value;if(mode==='monthly'){const d=new Date($('metrics-date').value.slice(0,7)+'-01T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+direction);$('metrics-date').value=d.toISOString().slice(0,10);}else $('metrics-date').value=shift($('metrics-date').value,direction*(mode==='weekly'?7:1));page=0;activeMetric='';render();});
    $('metrics-prev-page').addEventListener('click',()=>{page--;render();});$('metrics-next-page').addEventListener('click',()=>{page++;render();});
    $('metrics-export').addEventListener('click',()=>{if(!current)return;const rows=[['Company','Title','URL','Status','Verdict','First discovered','Last attempt'],...[...current.states.values()].filter(s=>current.activityKeys.has(s.key)||current.discovered.has(s.key)).map(s=>[s.job.company,s.job.title,safeURL(s.job.url),s.status,labels[s.verdict]||'',s.discovered,s.last_attempt])];const csv=rows.map(row=>row.map(v=>'"'+String(v??'').replace(/^[=+@-]/,m=>"'"+m).replace(/"/g,'""')+'"').join(',')).join('\r\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='jobsearch-metrics-'+selectedRange().start+'.csv';a.click();URL.revokeObjectURL(a.href);});
  }
  init();
})(globalThis);
