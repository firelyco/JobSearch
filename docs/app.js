const STORAGE_KEY = 'jobsearch.status.v1';
const PASS_KEY = 'jobsearch.gate.v1';
const PASSPHRASE_HASH = null;

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

let allJobs = [];
let metaData = {};

async function loadData() {
  try {
    const [jobsResp, metaResp] = await Promise.all([
      fetch(`jobs.json?t=${Date.now()}`),
      fetch(`meta.json?t=${Date.now()}`).catch(() => null),
    ]);
    if (!jobsResp.ok) throw new Error(`jobs.json HTTP ${jobsResp.status}`);
    allJobs = await jobsResp.json();
    if (metaResp && metaResp.ok) metaData = await metaResp.json();
  } catch (e) {
    console.error('load failed', e);
    $('jobs-tbody').innerHTML = `<tr><td colspan="5" class="empty">Could not load jobs.json. The poller may not have run yet.</td></tr>`;
    return;
  }
  render();
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

function renderTable() {
  const search = $('search').value.toLowerCase().trim();
  const source = $('filter-source').value;
  const status = $('filter-status').value;
  const scoreFilter = $('filter-score').value;
  const statuses = loadStatuses();

  let filtered = allJobs.filter(j => {
    if (source && j.source !== source) return false;
    if (scoreFilter === 'hot' && j.score < 85) return false;
    if (scoreFilter === 'standard' && j.score < 70) return false;
    const curStatus = statuses[jobKey(j)] || 'new';
    if (status && curStatus !== status) return false;
    if (search) {
      const blob = `${j.title} ${j.company} ${j.location}`.toLowerCase();
      if (!blob.includes(search)) return false;
    }
    return true;
  });

  const tbody = $('jobs-tbody');
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty">No jobs match your filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(j => {
    const bucket = scoreBucket(j.score || 0);
    const curStatus = statuses[jobKey(j)] || 'new';
    const meta = [j.location, j.source].filter(Boolean).join(' · ');
    return `
      <tr data-key="${escapeAttr(jobKey(j))}">
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

function bindControls() {
  ['search', 'filter-source', 'filter-status', 'filter-score'].forEach(id => {
    $(id).addEventListener('input', renderTable);
    $(id).addEventListener('change', renderTable);
  });
  $('export-btn').addEventListener('click', exportStatus);
  $('import-btn').addEventListener('click', () => $('import-file').click());
  $('import-file').addEventListener('change', (e) => {
    if (e.target.files[0]) importStatus(e.target.files[0]);
  });
}

(async function init() {
  bindGate();
  bindControls();
  const passed = await gateCheck();
  if (passed) loadData();
})();
