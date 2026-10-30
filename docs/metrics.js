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
      s.job={...s.job,...Object.fromEntries(Object.entries(e.job||{}).filter(([key,value])=>value!==null&&value!==undefined&&value!==''&&!(key==='title'&&value==='Posting details not recorded')))};s.latest=e.at;
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
  function postingURL(value){
    const url=safeURL(value);if(!url)return '';
    const u=new URL(url);u.hash='';u.hostname=u.hostname.replace(/^www\./,'');
    for(const name of [...u.searchParams.keys()])if(/^(utm_|trk$|tracking|ref$|source$|gh_src$)/i.test(name))u.searchParams.delete(name);
    u.searchParams.sort();return u.href.replace(/\/$/,'');
  }
  function canonicalEvents(input){
    const events=unique(input),parent=new Map(),byURL=new Map();
    function find(key){if(!parent.has(key))parent.set(key,key);const p=parent.get(key);if(p!==key)parent.set(key,find(p));return parent.get(key);}
    for(const e of events){if(!e.job_key)continue;find(e.job_key);const url=postingURL(e.job?.url);if(url){if(byURL.has(url))parent.set(find(e.job_key),find(byURL.get(url)));else byURL.set(url,e.job_key);}}
    return events.map(e=>e.job_key?{...e,job_key:find(e.job_key)}:e);
  }
  function roleCategory(title=''){
    if(/\bpmo\b|portfolio|program management office|project management office/i.test(title))return 'PMO / Portfolio Leadership';
    if(/\b(?:director|head|vice president|vp|chief)\b/i.test(title))return 'Director / Head of TPM';
    if(/manager.*(?:program management|program managers|tpms)|people manager/i.test(title))return 'TPM Manager';
    if(/\b(?:principal|staff|distinguished)\b/i.test(title))return 'Principal / Staff TPM';
    if(/\b(?:senior|sr\.?|lead)\b/i.test(title)&&/program|\btpm\b/i.test(title))return 'Senior TPM';
    return 'Other';
  }
  const sourceNames={greenhouse:'Company careers (Greenhouse)',lever:'Company careers (Lever)',ashby:'Company careers (Ashby)',workday:'Company careers (Workday)',apple:'Apple careers',google:'Google careers',amazon_jobs:'Amazon careers',github:'GitHub careers',web_discovery:'Web search',remotive:'Remotive',arbeitnow:'Arbeitnow',linkedin_email:'LinkedIn email',a16z:'a16z portfolio',sequoia:'Sequoia portfolio',greylock:'Greylock portfolio',yc:'Y Combinator',techstars:'Techstars portfolio',harmonic:'Harmonic'};
  function sourceCategory(job={}){const value=job.discovery_source||(job.discovery_sources||[])[0]||job.source||'Not recorded';return sourceNames[value]||value;}
  function stateCategory(s){
    if(s.eligible===false||s.status==='excluded')return 'excluded';
    if(s.status==='completed'&&labels[s.verdict])return 'completed';
    if(s.status==='processing')return 'processing';
    if(['failed','unresolved'].includes(s.status)||s.error||s.eligible!==true&&s.in_queue!==true)return 'blocked';
    return 'waiting';
  }
  function overview(input,period,filter={}){
    const events=canonicalEvents(input),r=analyze(events,period,filter),cohort=new Set(),groups={};
    for(const name of ['discovered','completed','strong','medium','not','waiting','processing','blocked','excluded','backlogWaiting','backlogProcessing','backlogBlocked'])groups[name]=new Set();
    const snapshot=events.filter(e=>e.type==='queue_snapshot'&&day(e.at)<=period.end).at(-1);
    const queue=snapshot?new Map(snapshot.jobs.map(row=>[row.job_key,row])):null;
    // Resolve snapshot keys through the same alias mapping as job events.
    const aliases=new Map(events.filter(e=>e.job_key).map(e=>[e.job?.url&&postingURL(e.job.url),e.job_key]));
    const originalAliases=new Map();for(const e of input)if(e.job_key&&e.job?.url&&aliases.has(postingURL(e.job.url)))originalAliases.set(e.job_key,aliases.get(postingURL(e.job.url)));
    if(queue)for(const [key,value] of [...queue])if(originalAliases.has(key)&&originalAliases.get(key)!==key){queue.delete(key);queue.set(originalAliases.get(key),value);}
    const firstSources=new Map();for(const e of events)if(e.type==='discovered'&&!firstSources.has(e.job_key))firstSources.set(e.job_key,sourceCategory(e.job));
    for(const s of r.states.values()){
      if(!matches(s.job,filter))continue;
      const observation=queue?.get(s.key);
      if(observation&&s.latest<=snapshot.at){
        s.status={assessed:'completed',pending:'waiting',in_progress:'processing',unresolved:'unresolved'}[observation.status]||s.status;
        if(observation.verdict)s.verdict=observation.verdict;
        s.eligible=true;s.in_queue=true;
        if(['assessed','pending','in_progress'].includes(observation.status))s.error='';
        if(observation.status==='unresolved')s.reason='Retry limit reached';
      }
      if(queue&&!observation&&s.latest<=snapshot.at&&s.status!=='completed'){
        s.eligible=false;s.in_queue=false;s.screening_reason='Not in active assessment scope at this checkpoint';
      }
      const category=stateCategory(s);s.category=category;
      s.discoverySource=firstSources.get(s.key)||sourceCategory(s.job);s.roleCategory=roleCategory(s.job.title);
      if(!s.discovered||day(s.discovered)<period.start||day(s.discovered)>period.end){
        if((!queue||queue.has(s.key)||s.latest>snapshot.at)&&s.in_queue!==false&&s.retained!==false&&(s.eligible===true||s.in_queue===true)&&['waiting','processing','blocked'].includes(category))groups['backlog'+category[0].toUpperCase()+category.slice(1)].add(s.key);
        continue;
      }
      cohort.add(s.key);groups.discovered.add(s.key);groups[category].add(s.key);
      if(category==='completed')groups[s.verdict].add(s.key);
    }
    function pivot(field){
      const rows=new Map();
      for(const key of cohort){const state=r.states.get(key),name=state[field];if(!rows.has(name))rows.set(name,{name,groups:Object.fromEntries(['discovered','completed','strong','medium','not','waiting','processing','blocked','excluded'].map(k=>[k,new Set()]))});const row=rows.get(name);row.groups.discovered.add(key);row.groups[state.category].add(key);if(state.category==='completed')row.groups[state.verdict].add(key);}
      return [...rows.values()].sort((a,b)=>b.groups.discovered.size-a.groups.discovered.size||a.name.localeCompare(b.name));
    }
    const total=cohort.size,eligible=total-groups.excluded.size,completed=groups.completed.size;
    return {analysis:r,groups,total,eligible,completed,queueAsOf:snapshot?.at,percent:eligible?Math.round(100*completed/eligible):0,sourceRows:pivot('discoverySource'),roleRows:pivot('roleCategory')};
  }
  function refreshCoverage(input,period,now=new Date()){
    const refreshes=unique(input).filter(e=>e.type==='refresh'),latest=refreshes.at(-1)?.at;
    const hasRefresh=refreshes.some(e=>day(e.at)>=period.start&&day(e.at)<=period.end);
    return {latest,hasRefresh,awaitingToday:period.start===day(now)&&period.end===day(now)&&!hasRefresh};
  }
  const API={day,range,shift,unique,safeURL,analyze,overview,canonicalEvents,roleCategory,sourceCategory,refreshCoverage};root.JobMetrics=API;
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id),esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let history=[],loaded=false,loading=false,loadFailed=false,activeMetric='',page=0,current=null,pivotSelection=null,currentKeys=new Set(),emailReport=null;
  const PAGE_SIZE=50;
  function selectedRange(){return range($('metrics-period').value,$('metrics-date').value,$('metrics-end').value);}
  function stamp(value){return value?new Intl.DateTimeFormat('en-US',{timeZone:TZ,dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'Not recorded';}
  const statusLabel=s=>({completed:'Completed',failed:'Assessment failed',waiting:'Waiting / not assessed',processing:'Processing',excluded:'Excluded during screening',unresolved:'Retry limit reached'}[s]||s);
  function salaryLabel(value){return {below_floor:'Published base max below $250K',meets_floor:'Base clears $250K',potentially_meets_floor:'Base range reaches $250K',unknown:'Base pay unverified'}[value]||'Base pay unverified';}
  function render(){
    if(!loaded)return;
    const period=selectedRange();
    if(!period.start||!period.end||period.end<period.start){$('metrics-status').textContent='Choose a valid date range.';return;}
    if((new Date(period.end)-new Date(period.start))/86400000>366){$('metrics-status').textContent='Choose a period of 366 days or less.';return;}
    const filters={company:$('metrics-company').value.trim(),role:$('metrics-role').value.trim(),source:$('metrics-source').value,location:$('metrics-location').value};
    const summary=overview(history,period,filters),r=summary.analysis;current=r;
    const groups=summary.groups,coverage=refreshCoverage(history,period),awaiting=coverage.awaitingToday&&summary.total===0;
    $('metrics-range-label').textContent=`${period.start===period.end?period.start:period.start+' through '+period.end} · Eastern time`;
    $('metrics-status').textContent=loadFailed?'Some history files could not be loaded. Counts are incomplete; refresh to retry.':coverage.awaitingToday?"Today's job refresh has not completed. Scheduled for 6 AM Eastern; these are not confirmed zero results.":r.partial?'Historical records are incomplete for this period. Counts show recorded jobs only.':'Jobs first found in this period. Status is shown as of the selected period end; today updates automatically.';
    $('metrics-updated').textContent=coverage.latest?'Last completed job refresh: '+stamp(coverage.latest):'No completed job refresh recorded.';

    const cardNames={discovered:'New jobs found',completed:'Assessment completed',strong:'Strong',medium:'Middle',not:'Pass',waiting:'Waiting',processing:'Processing',blocked:'Blocked',excluded:'Excluded',backlogWaiting:'Earlier backlog: waiting',backlogProcessing:'Earlier backlog: processing',backlogBlocked:'Earlier backlog: blocked'};
    function button(key,label=cardNames[key],cls='metrics-queue-count'){return `<button type="button" class="${cls} ${activeMetric===key&&!pivotSelection?'selected':''}" data-metric="${key}" aria-pressed="${activeMetric===key&&!pivotSelection}"><span>${esc(label)}</span><strong>${awaiting&&cls==='metrics-stat'?'—':groups[key].size.toLocaleString()}</strong>${cls==='metrics-stat'?'<small>'+(awaiting?'Awaiting refresh':'View jobs')+'</small>':''}</button>`;}
    $('metrics-cards').innerHTML=['discovered','completed','strong','medium','not'].map(k=>button(k,cardNames[k],'metrics-stat')).join('');
    $('metrics-progress').innerHTML=`<div class="metrics-progress-heading"><strong>${awaiting?'Awaiting today’s refresh':summary.completed+' / '+summary.eligible+' jobs completed'}</strong><span>${awaiting?'Not started':summary.eligible?summary.percent+'%':'No eligible jobs'}</span></div><progress aria-label="Assessment completion for jobs found in selected period" max="${summary.eligible||1}" value="${summary.completed}"></progress><div class="metrics-queue">${['waiting','processing','blocked','excluded'].map(k=>button(k)).join('')}</div><p class="muted">Excluded jobs are outside your criteria and do not enter the progress total. Blocked includes jobs awaiting eligibility confirmation.</p>`;
    const overall={waiting:groups.waiting.size+groups.backlogWaiting.size,processing:groups.processing.size+groups.backlogProcessing.size,blocked:groups.blocked.size+groups.backlogBlocked.size};
    $('metrics-backlog').innerHTML=`<strong>Overall outstanding queue: ${overall.waiting+overall.processing+overall.blocked}</strong><span>${overall.waiting} waiting · ${overall.processing} processing · ${overall.blocked} blocked${summary.queueAsOf?' · Queue updated '+esc(stamp(summary.queueAsOf)):''}</span><div class="metrics-queue">${['backlogWaiting','backlogProcessing','backlogBlocked'].map(k=>button(k)).join('')}</div>`;
    const columns=['discovered','completed','strong','medium','not','waiting','processing','blocked','excluded'];
    function pivot(rows,type){
      const cell=(keys,index,key)=>`<td><button type="button" class="metrics-number" data-pivot="${type}" data-row="${index}" data-column="${key}" aria-label="${esc((index<0?'Total':rows[index].name)+': '+cardNames[key]+', '+keys.size+' jobs')}">${keys.size}</button></td>`;
      if(awaiting)return '<tr><td colspan="10">Awaiting today’s job refresh. Results will appear automatically when it completes.</td></tr>';
      return rows.map((row,index)=>`<tr><th scope="row">${esc(row.name)}</th>${columns.map(key=>cell(row.groups[key],index,key)).join('')}</tr>`).join('')+`<tr class="metrics-total"><th scope="row">Total</th>${columns.map(key=>cell(groups[key],-1,key)).join('')}</tr>`;
    }
    $('metrics-source-pivot').innerHTML=pivot(summary.sourceRows,'source');
    $('metrics-role-pivot').innerHTML=pivot(summary.roleRows,'role');
    $('metrics-view-panel').querySelectorAll('[data-metric]').forEach(b=>b.addEventListener('click',()=>{activeMetric=activeMetric===b.dataset.metric&&!pivotSelection?'':b.dataset.metric;pivotSelection=null;page=0;render();$('metrics-job-section').scrollIntoView({block:'start',behavior:'smooth'});}));
    $('metrics-view-panel').querySelectorAll('[data-pivot]').forEach(b=>b.addEventListener('click',()=>{const rows=b.dataset.pivot==='source'?summary.sourceRows:summary.roleRows,index=Number(b.dataset.row);pivotSelection={kind:b.dataset.pivot,name:index<0?'Total':rows[index].name,column:b.dataset.column};activeMetric='';page=0;render();$('metrics-job-section').scrollIntoView({block:'start',behavior:'smooth'});}));
    let keys=groups[activeMetric]||groups.discovered,label=cardNames[activeMetric]||'New jobs found';
    if(pivotSelection){const rows=pivotSelection.kind==='source'?summary.sourceRows:summary.roleRows;keys=pivotSelection.name==='Total'?groups[pivotSelection.column]:rows.find(row=>row.name===pivotSelection.name)?.groups[pivotSelection.column]||new Set();label=pivotSelection.name+' · '+cardNames[pivotSelection.column];}
    const rows=[...keys].map(k=>r.states.get(k)).filter(Boolean).sort((a,b)=>b.latest.localeCompare(a.latest));currentKeys=keys;
    const totalPages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));page=Math.min(page,totalPages-1);
    $('metrics-jobs-label').textContent=`${label} · ${rows.length} jobs · page ${page+1} of ${totalPages}`;
    $('metrics-prev-page').disabled=page===0;$('metrics-next-page').disabled=page>=totalPages-1;
    $('metrics-jobs').innerHTML=rows.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE).map(s=>{
      const link=safeURL(s.job.url),reason=s.category==='excluded'?s.screening_reason||'Outside current criteria':s.category==='blocked'?[s.error,s.reason,s.eligible!==true?'Eligibility not confirmed':''].filter(Boolean).join(' · '):'';
      const events=(r.timeline.get(s.key)||[]).slice().reverse();
      const timeline=events.map(e=>`<li><time>${esc(stamp(e.at))}</time> — ${esc(e.type.replaceAll('_',' '))}${e.verdict?' · '+esc(labels[e.verdict]||e.verdict):''}${e.error?' · '+esc(e.error):''}${e.reason?' · '+esc(e.reason):''}${e.run_url&&safeURL(e.run_url)?` · <a href="${esc(safeURL(e.run_url))}" target="_blank" rel="noopener">Run</a>`:''}</li>`).join('');
      return `<tr data-metrics-key="${esc(s.key)}"><td>${link?`<a href="${esc(link)}" target="_blank" rel="noopener">${esc(s.job.title||'Open posting')}</a>`:esc(s.job.title||'Posting details not recorded')}<div class="muted">${esc(s.job.company||s.key.split(':')[1])} · ${esc(s.discoverySource)}</div><div class="muted">${esc(s.job.location||'Location not recorded')}</div><small>Employer source: ${esc(s.job.source||'Not recorded')}${s.job.discovery_sources?.length?' · Also found via '+esc(s.job.discovery_sources.join(', ')):''}</small></td><td>${esc(s.discovered?day(s.discovered):'Not recorded')}<br><small>Posted ${esc(s.job.posted_at?day(s.job.posted_at):'not recorded')}</small></td><td>${esc({completed:'Completed',blocked:'Blocked',waiting:'Waiting',processing:'Processing',excluded:'Excluded'}[s.category]||statusLabel(s.status))}<div class="muted">${esc(reason)}</div></td><td>${esc(s.category==='completed'?labels[s.verdict]:'—')}<div class="muted">${esc(salaryLabel(s.salary))}</div></td><td><details><summary>History (${events.length})</summary><ol class="metrics-timeline">${timeline}</ol></details></td></tr>`;
    }).join('')||'<tr><td colspan="5" class="empty">No jobs in this view.</td></tr>';
    renderEmailLinks(period);
  }
  function renderEmailLinks(period){
    if(!emailReport){$('metrics-email-label').textContent='Job links awaiting verification: unavailable';$('metrics-email-jobs').innerHTML='';return;}
    const rows=(emailReport.rows||[]).filter(row=>!['tracked','screened_out'].includes(row.collection)&&day(row.first_alert_at)>=period.start&&day(row.first_alert_at)<=period.end);
    $('metrics-email-label').textContent=`Job links awaiting verification: ${rows.length} (separate from confirmed jobs)`;
    $('metrics-email-jobs').innerHTML=rows.map(row=>`<tr><td>${safeURL(row.url)?`<a href="${esc(safeURL(row.url))}" target="_blank" rel="noopener">${esc(row.title||'LinkedIn job '+row.linkedin_id)}</a>`:'Job link unavailable'}<div class="muted">${esc(row.company||'Employer not identified')}</div></td><td>${row.collection==='possible_match'?'Confirm employer requisition':'Find employer posting'}</td></tr>`).join('')||'<tr><td colspan="2">No unverified links recorded for this discovery period.</td></tr>';
  }
  async function load(){
    if(loading)return;loading=true;$('metrics-status').textContent='Loading saved history…';
    try{
      const response=await fetch('metrics/index.txt?t='+Date.now());if(!response.ok)throw Error('History index is unavailable');
      const paths=[...new Set((await response.text()).trim().split(/\r?\n/))].filter(p=>/^\d{4}-\d{2}\/[a-zA-Z0-9_-]+\.json$/.test(p));
      const next=[];let failures=0,cursor=0;
      await Promise.all(Array.from({length:Math.min(6,paths.length)},async()=>{while(cursor<paths.length){const p=paths[cursor++];try{const r=await fetch('metrics/'+p+'?t='+Date.now());if(!r.ok)throw Error();const d=await r.json();if(!Array.isArray(d.events))throw Error();next.push(...d.events);}catch{failures++;}}}));
      history=unique(next);loaded=true;loadFailed=failures>0;
      try{const response=await fetch('email_alerts.json?t='+Date.now());emailReport=response.ok?await response.json():null;}catch{emailReport=null;}
      const previous=$('metrics-source').value,sources=[...new Set(history.map(e=>e.job?.source).filter(Boolean))].sort();
      $('metrics-source').innerHTML='<option value="">All sources</option>'+sources.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');$('metrics-source').value=previous;
      render();
    }catch(error){$('metrics-status').textContent='Could not load saved metrics. Use Refresh history to retry; the Jobs tab remains available.';}
    finally{loading=false;}
  }
  function init(){
    $('metrics-tab').tabIndex=-1;const today=day(new Date());$('metrics-date').value=today;$('metrics-end').value=today;
    function show(metrics){$('jobs-view').classList.toggle('hidden',metrics);$('metrics-view-panel').classList.toggle('hidden',!metrics);$('jobs-tab').setAttribute('aria-selected',String(!metrics));$('jobs-tab').tabIndex=metrics?-1:0;$('metrics-tab').tabIndex=metrics?0:-1;$('metrics-tab').setAttribute('aria-selected',String(metrics));if(metrics)load();}
    $('jobs-tab').addEventListener('click',()=>show(false));$('metrics-tab').addEventListener('click',()=>show(true));
    document.querySelector('.dashboard-tabs').addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const metrics=$('metrics-tab').getAttribute('aria-selected')!=='true';show(metrics);$(metrics?'metrics-tab':'jobs-tab').focus();}});
    $('metrics-refresh').addEventListener('click',load);
    ['metrics-period','metrics-date','metrics-end','metrics-company','metrics-role','metrics-source','metrics-location'].forEach(id=>$(id).addEventListener('input',()=>{page=0;activeMetric='';pivotSelection=null;$('metrics-end-label').classList.toggle('hidden',$('metrics-period').value!=='custom');render();}));
    for(const [id,direction] of [['metrics-previous',-1],['metrics-next',1]])$(id).addEventListener('click',()=>{const mode=$('metrics-period').value;if(mode==='monthly'){const d=new Date($('metrics-date').value.slice(0,7)+'-01T12:00:00Z');d.setUTCMonth(d.getUTCMonth()+direction);$('metrics-date').value=d.toISOString().slice(0,10);}else $('metrics-date').value=shift($('metrics-date').value,direction*(mode==='weekly'?7:1));page=0;activeMetric='';pivotSelection=null;render();});
    $('metrics-prev-page').addEventListener('click',()=>{page--;render();});$('metrics-next-page').addEventListener('click',()=>{page++;render();});
    $('metrics-clear').addEventListener('click',()=>{activeMetric='';pivotSelection=null;page=0;render();});
    $('metrics-export').addEventListener('click',()=>{if(!current)return;const rows=[['Company','Title','URL','Status','Verdict','First discovered','Source'],...[...currentKeys].map(k=>current.states.get(k)).filter(Boolean).map(s=>[s.job.company,s.job.title,safeURL(s.job.url),s.category,s.category==='completed'?labels[s.verdict]:'',s.discovered,s.discoverySource])];const csv=rows.map(row=>row.map(v=>'"'+String(v??'').replace(/^[=+@-]/,m=>"'"+m).replace(/"/g,'""')+'"').join(',')).join('\r\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='jobsearch-metrics-'+selectedRange().start+'.csv';a.click();URL.revokeObjectURL(a.href);});
    setInterval(()=>{if(!document.hidden&&!$('metrics-view-panel').classList.contains('hidden'))load();},60000);
  }
  init();
})(globalThis);
