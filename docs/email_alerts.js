/* Public job-reference reconciliation only. No mailbox data or credentials. */
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function safeUrl(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';}catch{return '';}}
  function link(url,label){const clean=safeUrl(url);return clean?`<a href="${esc(clean)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`:esc(label);}
  function assessment(row,scores){
    const eligible=(row.matches||[]).filter(m=>m.assessment_date_eligible!==false);
    if(!eligible.length&&row.matches?.length)return 'Outside assessment date scope';
    const values=eligible.map(m=>scores[m.key]).filter(Boolean);
    const valid=values.filter(s=>s.validated===true&&!s.refresh_pending&&['strong','medium','not'].includes(s.recommendation));
    if(valid.length)return [...new Set(valid.map(s=>({strong:'Strong',medium:'Middle',not:'Pass'}[s.recommendation])))].join(', ');
    return values.length?'Assessment pending / needs review':row.collection==='screened_out'?'Excluded from intake':'Not assessed';
  }
  const collection={tracked:'Exact posting captured',possible_match:'Possible match — confirm requisition',missing:'Missing — employer link unresolved',screened_out:'Screened out — see reason'};
  function render(report,scores={},page=1){
    const rows=report.rows||[],health=report.connection||{},count=report.counts||{},size=25,pages=Math.max(1,Math.ceil(rows.length/size));page=Math.min(page,pages);
    const status={not_configured:'Yahoo connection needs setup',failed:'Yahoo scan failed — last known results retained',partial:'Yahoo scan needs review — see message counts below',ok:'Yahoo scan completed'}[health.status]||'Waiting for first Yahoo scan';
    const scanned=health.messages_checked!=null?` ${health.messages_checked} messages checked; ${health.messages_deferred||0} deferred; ${health.failed_messages||0} failed; ${health.unresolved_alert_messages||0} alert emails need link-format review.`:'';
    return `<p><strong>${esc(status)}.</strong>${esc(scanned)} ${esc(health.reason||'')}</p><p>${rows.length} unique job references: ${count.tracked||0} exact matches, ${count.possible_match||0} possible matches, ${count.missing||0} unresolved, ${count.screened_out||0} screened out.</p>
      <p>Last complete scan: ${report.last_success_at?esc(new Date(report.last_success_at).toLocaleString()):'Not yet completed'}. One-time 14-day catch-up; daily job search imports only new, unprocessed alert emails. Collected references are retained. An alert date is not a job posting date.</p>
      <p>${esc(report.scope||'Coverage is limited to received job-alert emails.')} Email bodies and recipients are not published. <a href="https://jobsearch-history.vercel.app/email-alert-setup.html" target="_blank" rel="noopener">Connection setup</a></p>
      ${rows.length?`<div style="overflow-x:auto"><table><thead><tr><th>Job alert</th><th>Collection</th><th>Assessment</th><th>Employer posting</th></tr></thead><tbody>${rows.slice((page-1)*size,page*size).map(row=>`<tr><td>${link(row.url,row.title||'LinkedIn job '+row.linkedin_id)}<br>${esc(row.company||'Employer not identified in email')}</td><td>${esc(collection[row.collection]||row.collection)}${row.reason?'<br>'+esc(row.reason):''}</td><td>${esc(assessment(row,scores))}</td><td>${(row.matches||[]).map(m=>link(m.url,m.title||'Employer posting')).join('<br>')||'Needs employer URL resolution'}</td></tr>`).join('')}</tbody></table></div><p>Page ${page} of ${pages} <button type="button" data-email-page="${page-1}" ${page===1?'disabled':''}>Previous</button> <button type="button" data-email-page="${page+1}" ${page===pages?'disabled':''}>Next</button></p>`:'<p>No job references imported yet. A zero count does not confirm coverage until a scan completes.</p>'}`;
  }
  let current=null,scores={},page=1;
  function paint(){const target=document.getElementById('email-alert-status');if(target&&current)target.innerHTML=render(current,scores,page);}
  async function load(nextScores){scores=nextScores||{};try{const response=await fetch('email_alerts.json?t='+Date.now());if(response.ok)current=await response.json();else current={connection:{status:'not_configured'},rows:[]};paint();}catch{const target=document.getElementById('email-alert-status');if(target)target.textContent='Job-alert coverage report is unavailable.';}}
  if(typeof window!=='undefined'){window.EmailAlerts={load,render,assessment};document.addEventListener('DOMContentLoaded',()=>{if(!current)load(typeof fitScores!=='undefined'?fitScores:{});});document.addEventListener('click',event=>{const button=event.target.closest('[data-email-page]');if(button&&!button.disabled){page=Number(button.dataset.emailPage);paint();}});}
  if(typeof module!=='undefined')module.exports={render,assessment,safeUrl};
})();
