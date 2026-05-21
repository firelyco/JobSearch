const STORAGE_KEY = 'jobsearch.status.v1';
const PASS_KEY = 'jobsearch.gate.v1';
const PAT_KEY = 'jobsearch.pat.v1';
const TAILORING_KEY = 'jobsearch.tailoring.v1';
const PASSPHRASE_HASH = null;

const REPO_OWNER = 'firelyco';
const REPO_NAME = 'JobSearch';
const POLL_INTERVAL_MS = 10000;
const TAILORING_TIMEOUT_MS = 5 * 60 * 1000;

const $ = (id) => document.getElementById(id);

async function sha256Hex(s) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

function loadStatuses() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch { return {}; }
}
function saveStatuses(s) { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); }
function setStatus(jobKey, status) {
  const s = loadStatuses();
  if (!status) delete s[jobKey];
  else s[jobKey] = status;
  saveStatuses(s);
}

function jobKey(j) { return `${j.source}:${j.company}:${j.id}`; }

function relativeTime(iso) {
  if (!iso) return '—';
  const then = new Date(iso);
  if (isNaN(then)) return '—';
  const secs = Math.floor((Date.now() - then.getTime()) / 1000);
  if (secs < 60) return 'just now';
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  const days = Math.floor(secs / 86400);
  if (days < 30) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}

function scoreBucket(score) {
  if (score >= 85) return 'hot';
  if (score >= 70) return 'standard';
  return 'low';
}

const ROLE_CATEGORIES = [
  { id: 'vp',         pattern: /\b(vp|vice\s+president|head\s+of)\b.*\bprogram/i },
  { id: 'director',   pattern: /\bdirector\b.*(\btpm\b|technical\s+program|program\s+management)/i },
  { id: 'sr_manager', pattern: /(sr\.?\s*manager|senior\s+manager).*(\btpm\b|technical\s+program|program\s+management)/i },
  { id: 'manager',    pattern: /\bmanager\b.*(\btpm\b|technical\s+program|program\s+management)/i },
  { id: 'principal',  pattern: /(principal|staff)\s+(technical\s+program\s+manager|tpm)/i },
];

let allJobs = [];
let metaData = {};
let tailoredJobs = {};       // { jobKey: { tailored_at, run_id, ... } } from docs/tailored_jobs.json
let pollTimer = null;

// ---------- PAT management ----------
function getPAT() { return localStorage.getItem(PAT_KEY) || ''; }
function setStoredPAT(value) { localStorage.setItem(PAT_KEY, value); }
function clearStoredPAT() { localStorage.removeItem(PAT_KEY); }

// ---------- In-flight tailoring tracking ----------
function loadInFlight() {
  try { return JSON.parse(localStorage.getItem(TAILORING_KEY) || '{}'); }
  catch { return {}; }
}
function saveInFlight(state) { localStorage.setItem(TAILORING_KEY, JSON.stringify(state)); }
function markTailoringStarted(jobKey) {
  const s = loadInFlight();
  s[jobKey] = { dispatchedAt: Date.now() };
  saveInFlight(s);
}
function markTailoringFinished(jobKey) {
  const s = loadInFlight();
  delete s[jobKey];
  saveInFlight(s);
}

async function loadData() {
  try {
    const [jobsResp, metaResp, tailoredResp] = await Promise.all([
      fetch(`jobs.json?t=${Date.now()}`),
      fetch(`meta.json?t=${Date.now()}`).catch(() => null),
      fetch(`tailored_jobs.json?t=${Date.now()}`).catch(() => null),
    ]);
    if (!jobsResp.ok) throw new Error(`jobs.json HTTP ${jobsResp.status}`);
    allJobs = await jobsResp.json();
    if (metaResp && metaResp.ok) metaData = await metaResp.json();
    if (tailoredResp && tailoredResp.ok) {
      try { tailoredJobs = await tailoredResp.json(); } catch { tailoredJobs = {}; }
    }
  } catch (e) {
    console.error('load failed', e);
    $('jobs-tbody').innerHTML = `<tr><td colspan="6" class="empty">Could not load jobs.json. The poller may not have run yet.</td></tr>`;
    return;
  }
  render();
  if (Object.keys(loadInFlight()).length > 0) startPolling();
}

async function refreshTailoredJobs() {
  try {
    const r = await fetch(`tailored_jobs.json?t=${Date.now()}`);
    if (r.ok) {
      tailoredJobs = await r.json();
      return true;
    }
  } catch (e) {
    console.warn('tailored_jobs.json fetch failed', e);
  }
  return false;
}

function render() {
  renderMeta();
  renderMetrics();
  renderTable();
}

function renderMeta() {
  const ts = metaData.polled_at;
  if (ts) {
    $('meta-line').textContent = `last poll · ${relativeTime(ts)} ago · ${metaData.tracked_total || 0} jobs tracked`;
  } else {
    $('meta-line').textContent = '';
  }
  const hot = allJobs.filter(j => j.score >= 85).length;
  $('badge-hot').textContent = `${hot} hot`;
  $('badge-total').textContent = `${allJobs.length} total`;
}

function renderMetrics() {
  const statuses = loadStatuses();
  const dayAgo = Date.now() - 86400 * 1000;
  const weekAgo = Date.now() - 7 * 86400 * 1000;

  const newToday = allJobs.filter(j => {
    const ts = j.first_seen_at;
    return ts && new Date(ts).getTime() >= dayAgo;
  }).length;
  const newWeek = allJobs.filter(j => {
    const ts = j.first_seen_at;
    return ts && new Date(ts).getTime() >= weekAgo;
  }).length;
  const applied = Object.values(statuses).filter(s => s === 'applied').length;
  const interviewing = Object.values(statuses).filter(s => s === 'interviewing').length;

  $('m-today').textContent = newToday;
  $('m-week').textContent = newWeek;
  $('m-applied').textContent = applied;
  $('m-interviewing').textContent = interviewing;
}

function getSelectedRoles() {
  return ROLE_CATEGORIES.filter(r => {
    const cb = document.querySelector(`#role-panel input[data-role="${r.id}"]`);
    return cb && cb.checked;
  });
}

function renderTable() {
  const search = ($('role-search-text')?.value || '').toLowerCase().trim();
  const source = $('filter-source').value;
  const status = $('filter-status').value;
  const scoreFilter = $('filter-score').value;
  const statuses = loadStatuses();
  const selectedRoles = getSelectedRoles();

  let filtered = allJobs.filter(j => {
    if (source && j.source !== source) return false;
    if (scoreFilter === 'hot' && j.score < 85) return false;
    if (scoreFilter === 'standard' && j.score < 70) return false;
    const curStatus = statuses[jobKey(j)] || 'new';
    if (status && curStatus !== status) return false;
    const title = (j.title || '').toLowerCase();
    if (!selectedRoles.some(r => r.pattern.test(title))) return false;
    if (search) {
      const blob = `${j.title} ${j.company} ${j.location}`.toLowerCase();
      if (!blob.includes(search)) return false;
    }
    return true;
  });

  const tbody = $('jobs-tbody');
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">No jobs match your filters.</td></tr>`;
    return;
  }

  const inFlight = loadInFlight();
  tbody.innerHTML = filtered.map(j => {
    const bucket = scoreBucket(j.score || 0);
    const key = jobKey(j);
    const curStatus = statuses[key] || 'new';
    const meta = [j.location, j.source].filter(Boolean).join(' · ');
    return `
      <tr data-key="${escapeAttr(key)}">
        <td><span class="score-pill score-${bucket}">${j.score || 0}</span></td>
        <td>
          <div class="role-title"><a href="${escapeAttr(j.url)}" target="_blank" rel="noopener">${escapeHtml(j.title)}</a></div>
          <div class="role-meta">${escapeHtml(meta)}</div>
        </td>
        <td>${escapeHtml(j.company)}</td>
        <td>${relativeTime(j.first_seen_at || j.posted_at)}</td>
        <td>
          <select class="status-select">
            <option value="new" ${curStatus==='new'?'selected':''}>New</option>
            <option value="reviewing" ${curStatus==='reviewing'?'selected':''}>Reviewing</option>
            <option value="applied" ${curStatus==='applied'?'selected':''}>Applied</option>
            <option value="interviewing" ${curStatus==='interviewing'?'selected':''}>Interviewing</option>
            <option value="rejected" ${curStatus==='rejected'?'selected':''}>Rejected</option>
            <option value="not-interested" ${curStatus==='not-interested'?'selected':''}>Not interested</option>
          </select>
        </td>
        <td>${tailorCellHtml(key, tailoredJobs[key], inFlight[key])}</td>
      </tr>
    `;
  }).join('');

  tbody.querySelectorAll('.status-select').forEach(sel => {
    sel.addEventListener('change', (e) => {
      const row = e.target.closest('tr');
      const key = row.getAttribute('data-key');
      setStatus(key, e.target.value === 'new' ? null : e.target.value);
      renderMetrics();
    });
  });

  tbody.querySelectorAll('.tailor-btn, .retailor-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const row = e.target.closest('tr');
      onTailorClick(row.getAttribute('data-key'));
    });
  });
}

function tailorCellHtml(key, tailored, inflight) {
  if (inflight) {
    return `<span class="tailor-spinner" title="Dispatched ${relativeTime(new Date(inflight.dispatchedAt).toISOString())} ago">⟳ Tailoring…</span>`;
  }
  if (tailored && tailored.run_id) {
    const url = `https://github.com/${REPO_OWNER}/${REPO_NAME}/actions/runs/${escapeAttr(String(tailored.run_id))}#artifacts`;
    const bullets = tailored.bullets_count ?? '?';
    const dropped = tailored.dropped_count ?? 0;
    const meta = dropped > 0 ? `${bullets} bullets · ${dropped} dropped` : `${bullets} bullets`;
    return `
      <div class="tailor-cell">
        <a class="download-link" href="${url}" target="_blank" rel="noopener">↓ Download</a>
        <button class="retailor-btn btn-ghost-sm" type="button">Re-tailor</button>
        <div class="tailor-meta">${escapeHtml(meta)}</div>
      </div>
    `;
  }
  return `<button class="tailor-btn btn-primary-sm" type="button">Tailor →</button>`;
}

// ---------- Dispatch ----------
async function onTailorClick(key) {
  const pat = getPAT();
  if (!pat) {
    openPatModal({ pendingTailor: key });
    return;
  }
  try {
    await dispatchTailor(key, pat);
    markTailoringStarted(key);
    toast(`Tailoring ${key.split(':').slice(1).join(':')} — this takes ~60-90s`, 'info');
    renderTable();
    startPolling();
  } catch (e) {
    console.error('dispatch failed', e);
    if (String(e.message).includes('401') || String(e.message).includes('403')) {
      toast('PAT invalid or missing scope. Click PAT to update.', 'error');
    } else {
      toast(`Tailor dispatch failed: ${e.message}`, 'error');
    }
  }
}

async function dispatchTailor(key, pat) {
  const r = await fetch(`https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/dispatches`, {
    method: 'POST',
    headers: {
      'Accept': 'application/vnd.github+json',
      'Authorization': `Bearer ${pat}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      event_type: 'tailor_job',
      client_payload: { job_key: key },
    }),
  });
  if (r.status !== 204) {
    const body = await r.text().catch(() => '');
    throw new Error(`HTTP ${r.status} ${body.slice(0, 200)}`);
  }
}

// ---------- Polling ----------
function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollTailoringStatus, POLL_INTERVAL_MS);
  pollTailoringStatus();
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function pollTailoringStatus() {
  const inFlight = loadInFlight();
  const keys = Object.keys(inFlight);
  if (keys.length === 0) { stopPolling(); return; }

  // Time-out stale dispatches so the UI doesn't spin forever
  const now = Date.now();
  let dirty = false;
  for (const k of keys) {
    if (now - (inFlight[k].dispatchedAt || 0) > TAILORING_TIMEOUT_MS) {
      markTailoringFinished(k);
      toast(`Tailoring for ${k} timed out — check Actions tab`, 'error');
      dirty = true;
    }
  }
  if (dirty) { renderTable(); }
  if (Object.keys(loadInFlight()).length === 0) { stopPolling(); return; }

  // Refresh server-side state. If our in-flight job has a tailored_at timestamp
  // newer than the dispatch time, the workflow finished.
  const updated = await refreshTailoredJobs();
  if (!updated) return;
  const current = loadInFlight();
  let changed = false;
  for (const k of Object.keys(current)) {
    const entry = tailoredJobs[k];
    if (!entry) continue;
    const tailoredAtMs = Date.parse(entry.tailored_at || '');
    if (tailoredAtMs && tailoredAtMs >= current[k].dispatchedAt) {
      markTailoringFinished(k);
      toast(`Tailored ${k.split(':').slice(1).join(':')} ready to download`, 'success');
      changed = true;
    }
  }
  if (changed) renderTable();
  if (Object.keys(loadInFlight()).length === 0) stopPolling();
}

// ---------- PAT modal ----------
let _patModalContext = {};
function openPatModal(ctx = {}) {
  _patModalContext = ctx;
  $('pat-modal').classList.remove('hidden');
  const existing = getPAT();
  $('pat-input').value = '';
  $('pat-input').placeholder = existing ? '(token already saved — paste a new one to replace)' : 'github_pat_…';
  $('pat-modal-error').classList.add('hidden');
  setTimeout(() => $('pat-input').focus(), 0);
}
function closePatModal() {
  $('pat-modal').classList.add('hidden');
  _patModalContext = {};
}
function savePatFromModal() {
  const val = $('pat-input').value.trim();
  if (!val) {
    showPatError('Paste a token first.');
    return;
  }
  if (!/^(github_pat_|ghp_)/.test(val)) {
    showPatError('Looks invalid — expected to start with github_pat_ or ghp_.');
    return;
  }
  setStoredPAT(val);
  updatePatButtonLabel();
  closePatModal();
  toast('PAT saved', 'success');
  if (_patModalContext.pendingTailor) {
    onTailorClick(_patModalContext.pendingTailor);
  }
}
function showPatError(msg) {
  const el = $('pat-modal-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}
function updatePatButtonLabel() {
  $('pat-btn').textContent = getPAT() ? 'PAT ✓' : 'PAT';
}

// ---------- Toast ----------
function toast(msg, kind = 'info') {
  const container = $('toasts');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast toast-${kind}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.classList.add('toast-fade'), 4500);
  setTimeout(() => el.remove(), 5000);
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

function exportStatus() {
  const data = loadStatuses();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `jobsearch-status-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function importStatus(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const imported = JSON.parse(e.target.result);
      if (typeof imported !== 'object' || imported === null) throw new Error('not an object');
      const merged = { ...loadStatuses(), ...imported };
      saveStatuses(merged);
      render();
      alert(`Imported ${Object.keys(imported).length} status entries.`);
    } catch (err) {
      alert(`Import failed: ${err.message}`);
    }
  };
  reader.readAsText(file);
}

async function gateCheck() {
  if (!PASSPHRASE_HASH) {
    $('gate').classList.add('hidden');
    $('app').classList.remove('hidden');
    return true;
  }
  const stored = sessionStorage.getItem(PASS_KEY);
  if (stored === PASSPHRASE_HASH) {
    $('gate').classList.add('hidden');
    $('app').classList.remove('hidden');
    return true;
  }
  $('gate').classList.remove('hidden');
  $('app').classList.add('hidden');
  return false;
}

function bindGate() {
  const tryUnlock = async () => {
    const v = $('gate-input').value;
    const h = await sha256Hex(v);
    if (h === PASSPHRASE_HASH) {
      sessionStorage.setItem(PASS_KEY, h);
      $('gate-error').classList.add('hidden');
      $('gate').classList.add('hidden');
      $('app').classList.remove('hidden');
      loadData();
    } else {
      $('gate-error').classList.remove('hidden');
    }
  };
  $('gate-button').addEventListener('click', tryUnlock);
  $('gate-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') tryUnlock(); });
}

function updateRoleTriggerLabel() {
  const total = ROLE_CATEGORIES.length;
  const selected = getSelectedRoles().length;
  const label = selected === 0 ? 'Roles: none' : `Roles: ${selected} of ${total}`;
  $('role-trigger-label').textContent = label;
}

function bindRoleDropdown() {
  const trigger = $('role-trigger');
  const panel = $('role-panel');
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const isHidden = panel.classList.toggle('hidden');
    trigger.setAttribute('aria-expanded', String(!isHidden));
  });
  panel.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => {
    panel.classList.add('hidden');
    trigger.setAttribute('aria-expanded', 'false');
  });
  panel.querySelectorAll('input[data-role]').forEach(cb => {
    cb.addEventListener('change', () => {
      updateRoleTriggerLabel();
      renderTable();
    });
  });
  $('role-search-text').addEventListener('input', renderTable);
  updateRoleTriggerLabel();
}

function bindControls() {
  ['filter-source', 'filter-status', 'filter-score'].forEach(id => {
    $(id).addEventListener('input', renderTable);
    $(id).addEventListener('change', renderTable);
  });
  bindRoleDropdown();
  $('export-btn').addEventListener('click', exportStatus);
  $('import-btn').addEventListener('click', () => $('import-file').click());
  $('import-file').addEventListener('change', (e) => {
    if (e.target.files[0]) importStatus(e.target.files[0]);
  });

  // PAT modal
  $('pat-btn').addEventListener('click', () => openPatModal());
  $('pat-save-btn').addEventListener('click', savePatFromModal);
  $('pat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') savePatFromModal(); });
  $('pat-clear-btn').addEventListener('click', () => {
    clearStoredPAT();
    updatePatButtonLabel();
    toast('PAT cleared from this browser', 'info');
    closePatModal();
  });
  document.querySelectorAll('[data-close-modal]').forEach(el => {
    el.addEventListener('click', closePatModal);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('pat-modal').classList.contains('hidden')) closePatModal();
  });
  updatePatButtonLabel();
}

(async function init() {
  bindGate();
  bindControls();
  const passed = await gateCheck();
  if (passed) loadData();
})();
