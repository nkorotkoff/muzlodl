
async function api(path) {
  const r = await fetch(path, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) throw new Error(r.statusText);
  return r.json();
}

function escape(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

async function load() {
  const data = await api('/api/imports');
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = data.sessions.map(s => {
    const d = new Date(s.created_at);
    const dateStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    const flag = s.status === 'interrupted'
      ? ' <span class="badge badge-interrupted">interrupted</span>' : '';
    return `<tr>
      <td>${escape(dateStr)}</td>
      <td>${escape(s.source_name || s.source)}${flag}</td>
      <td>${s.total}</td>
      <td>${s.downloaded}</td>
      <td>${s.failed}</td>
      <td><button onclick="showDetail(${s.id},'${escape(s.source_name || s.source)}')">View</button></td>
    </tr>`;
  }).join('');
}

let detailState = { id: null, name: '', page: 0, limit: 100, total: 0, loading: false, done: false };
let detailScrollEl = null;

async function loadDetailPage() {
  const { id, name, limit } = detailState;
  const data = await api(`/api/imports/${id}?limit=${limit}&offset=0`);
  detailState.total = data.total;
  detailState.done = data.tracks.length < limit;
  document.getElementById('detail-title').textContent =
    `Import: ${name} (${data.total} tracks)`;
  const body = document.getElementById('detail-body');
  body.innerHTML = data.tracks.map(t => renderDetailRow(t)).join('');
}

async function loadDetailMore() {
  if (detailState.loading || detailState.done) return;
  detailState.loading = true;
  try {
    const { id, limit } = detailState;
    const offset = detailState.page * limit;
    const data = await api(`/api/imports/${id}?limit=${limit}&offset=${offset}`);
    detailState.page++;
    detailState.done = data.tracks.length < limit;
    const body = document.getElementById('detail-body');
    body.insertAdjacentHTML('beforeend', data.tracks.map(t => renderDetailRow(t)).join(''));
  } catch (e) { /* keep scrolling on next attempt */ }
  detailState.loading = false;
}

function renderDetailRow(t) {
  return `
    <tr>
      <td>${escape(t.artist)}</td>
      <td>${escape(t.title)}</td>
      <td><span class="badge badge-${t.status}">${t.status}</span></td>
    </tr>`;
}

function showDetail(id, name) {
  detailState = { id, name, page: 1, limit: 100, total: 0, loading: false, done: false };
  const modal = document.getElementById('detail-modal');
  modal.style.display = 'flex';
  // Rebind scroll: the content wrapper scrolls, not the page.
  if (detailScrollEl) detailScrollEl.removeEventListener('scroll', onDetailScroll);
  detailScrollEl = modal.querySelector('.modal-content');
  detailScrollEl.addEventListener('scroll', onDetailScroll);
  loadDetailPage().catch(e => toast('Error: ' + e.message, true));
}

function onDetailScroll() {
  if (!detailScrollEl) return;
  if (detailScrollEl.scrollTop + detailScrollEl.clientHeight >= detailScrollEl.scrollHeight - 120) {
    loadDetailMore();
  }
}

function closeDetail() {
  const modal = document.getElementById('detail-modal');
  modal.style.display = 'none';
  if (detailScrollEl) { detailScrollEl.removeEventListener('scroll', onDetailScroll); detailScrollEl = null; }
}

load();
// Live progress: session counters (downloaded/failed) now update as the
// job runs, so refresh the table every 10s.
setInterval(() => { try { load(); } catch (e) {} }, 10000);

// ---- Background jobs widget ----
function escape(s) { const d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }
async function pollJobs() {
  try {
    const r = await fetch("/api/jobs");
    if (!r.ok) return;
    const d = await r.json();
    renderJobs(d.jobs || [], d.running || 0);
  } catch (e) {}
}
function renderJobs(jobs, running) {
  const toggle = document.getElementById("jobs-toggle");
  if (!toggle) return;
  if (!running && !jobs.length) {
    toggle.style.display = "none";
    const p = document.getElementById("jobs-panel");
    if (p) p.style.display = "none";
    return;
  }
  toggle.style.display = "inline-block";
  document.getElementById("jobs-count").textContent = running;
  const panel = document.getElementById("jobs-panel");
  const isOpen = panel.style.display === "block";
  panel.innerHTML = jobs.map(j => {
    const p = j.progress || {ok: 0, failed: 0, total: 0};
    const pct = p.total > 0 ? Math.round((p.ok + p.failed) / p.total * 100) : 0;
    const statusIcon = j.done ? (j.error ? "❌" : "✅") : (j.cancelled ? "⏹" : "⏳");
    const detail = j.done && j.error
      ? "<span class=\"job-error\">" + escape(j.error) + "</span>"
      : "<span class=\"job-meta\">" + p.ok + " done" + (p.failed ? ", " + p.failed + " failed" : "") + " / " + p.total + "</span>";
    const stop = j.done ? "" : "<button class=\"btn-job-stop\" onclick=\"cancelJob('" + j.id + "')\" title=\"Stop job\">⏹</button>";
    return "<div class=\"job-item\"><div class=\"job-head\"><span>" + statusIcon + " " + escape(j.title) + "</span><span class=\"job-pct\">" + pct + "%</span>" + stop + "</div><div class=\"progress-bar\"><div class=\"progress-fill\" style=\"width:" + pct + "%\"></div></div>" + detail + "</div>";
  }).join("");
  if (isOpen) panel.style.display = "block";
}
async function cancelJob(id) {
  if (!confirm('Stop this job?')) return;
  try {
    await api(`/api/download/${id}`, { method: 'DELETE' });
    toast('Job stopped');
  } catch (e) { toast('Error: ' + e.message, true); }
}
function toggleJobsPanel() {
  const panel = document.getElementById("jobs-panel");
  panel.style.display = panel.style.display === "block" ? "none" : "block";
  if (panel.style.display === "block") pollJobs();
}
setInterval(() => {
  const toggle = document.getElementById("jobs-toggle");
  if (toggle && toggle.style.display !== "none") pollJobs();
}, 2000);
pollJobs();

