
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) { const e = await r.json().catch(() => ({error: r.statusText})); throw new Error(e.error || r.statusText); }
  return r.json();
}

function escape(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function toast(msg, err = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = err ? 'toast-error' : 'toast-info';
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// ---- Settings ----

async function loadSettings() {
  const s = await api('/api/settings');
  document.getElementById('s-username').value = s.admin_username || 'admin';
  document.getElementById('s-password').value = '';
  document.getElementById('s-acoustid-key').value = s.acoustid_api_key || '';
  document.getElementById('s-acoustid-verify').checked = s.acoustid_verify === 'true';
  document.getElementById('s-acoustid-score').value = s.acoustid_min_score || '0.5';

  const sources = JSON.parse(s.sources || '[]');
  window._sources = sources;
  renderSources();
}

function renderSources() {
  const list = document.getElementById('sources-list');
  list.innerHTML = window._sources.map((src, i) => `
    <div class="source-row">
      <span class="source-name">${escape(src)}</span>
      <button onclick="moveSource(${i}, -1)" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button onclick="moveSource(${i}, 1)" ${i === window._sources.length - 1 ? 'disabled' : ''}>↓</button>
    </div>
  `).join('');
}

function moveSource(idx, dir) {
  const arr = window._sources;
  const other = idx + dir;
  if (other < 0 || other >= arr.length) return;
  [arr[idx], arr[other]] = [arr[other], arr[idx]];
  window._sources = arr;
  renderSources();
}

async function saveSettings() {
  const body = {
    admin_username: document.getElementById('s-username').value.trim(),
    password: document.getElementById('s-password').value,
    acoustid_api_key: document.getElementById('s-acoustid-key').value,
    acoustid_verify: document.getElementById('s-acoustid-verify').checked ? 'true' : 'false',
    acoustid_min_score: document.getElementById('s-acoustid-score').value,
    sources: JSON.stringify(window._sources || []),
  };
  await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) });
  document.getElementById('save-msg').textContent = '✅ Saved';
  setTimeout(() => document.getElementById('save-msg').textContent = '', 2000);
  toast('Settings saved');
}

// ---- Doctor ----

async function runDoctor() {
  const el = document.getElementById('doctor-results');
  el.style.display = 'block';
  el.innerHTML = '<p>Testing sources... (30-60 sec)</p>';
  try {
    const data = await api('/api/doctor');
    el.innerHTML = '<table><thead><tr><th>Source</th><th>Status</th><th>Search</th><th>Download</th><th>Latency</th></tr></thead><tbody>' +
      data.sources.map(s => `<tr>
        <td>${escape(s.name)}</td>
        <td>${escape(s.status)}</td>
        <td>${s.available ? '✅' : '❌'}</td>
        <td>${s.can_download ? '✅' : '❌'}</td>
        <td>${s.latency_ms}ms</td>
      </tr>`).join('') +
      '</tbody></table>';
  } catch (e) {
    el.innerHTML = `<p class="error">Error: ${escape(e.message)}</p>`;
  }
}

// ---- Cloud ----

async function checkCloud() {
  try {
    const s = await api('/api/cloud/status');
    const el = document.getElementById('cloud-status');
    if (!s.configured) {
      el.innerHTML = 'Not configured.';
      document.getElementById('cloud-show-setup').style.display = 'inline-block';
    } else {
      el.innerHTML = `Backend: <strong>${escape(s.backend)}</strong> · Reachable: <strong>${s.reachable ? '✅' : '❌'}</strong>`;
      document.getElementById('cloud-show-setup').style.display = 'inline-block';
    }
  } catch (e) {
    document.getElementById('cloud-status').textContent = 'Error: ' + e.message;
  }
}

async function saveCloudConfig() {
  const backend = document.getElementById('cloud-backend').value;
  const login = document.getElementById('cloud-login').value;
  const password = document.getElementById('cloud-password').value;
  const root = document.getElementById('cloud-root').value;

  if (backend === 'yandex_rest') {
    if (!password) { toast('Paste your OAuth token', true); return; }
  } else if (!login || !password) {
    toast('Fill login and password', true); return;
  }

  try {
    const r = await api('/api/cloud/config', {
      method: 'POST',
      body: JSON.stringify({ backend, login, password, root }),
    });
    toast('Cloud configured and tested ✅');
    checkCloud();
  } catch (e) {
    toast('Error: ' + e.message, true);
  }
}

// Toggle fields based on backend type
document.getElementById('cloud-backend').addEventListener('change', function() {
  const isRest = this.value === 'yandex_rest';
  document.getElementById('cloud-login-wrap').style.display = isRest ? 'none' : 'block';
  document.getElementById('cloud-password-label').textContent = isRest ? 'OAuth token' : 'App password';
  document.getElementById('cloud-rest-hint').style.display = isRest ? 'block' : 'none';
});

async function clearCloudConfig() {
  await api('/api/cloud/config', { method: 'DELETE' });
  toast('Cloud config cleared');
  checkCloud();
}

async function uploadCloud() {
  const el = document.getElementById('cloud-upload-status');
  el.textContent = 'Uploading...';
  try {
    const r = await api('/api/cloud/upload', { method: 'POST' });
    if (r.job_id) {
      const iv = setInterval(async () => {
        const s = await api(`/api/download/${r.job_id}`);
        const p = s.progress;
        if (p.total > 1) el.textContent = `Uploading: ${p.ok} / ${p.total} albums`;
        if (s.done) {
          clearInterval(iv);
          el.textContent = s.error ? `Error: ${s.error}` : '✅ Upload complete';
        }
      }, 1000);
    }
  } catch (e) {
    el.textContent = 'Error: ' + e.message;
  }
}

async function startReencode() {
  const bitrate = parseInt(document.getElementById('reencode-bitrate').value);
  if (!confirm(`Re-encode ALL files to ${bitrate} kbps Opus? This is lossy and irreversible.`)) return;
  const box = document.getElementById('reencode-progress');
  box.style.display = 'block';
  document.getElementById('reencode-fill').style.width = '0%';
  document.getElementById('reencode-text').textContent = 'Starting...';
  try {
    const r = await api('/api/library/reencode', { method: 'POST', body: JSON.stringify({bitrate}) });
    const iv = setInterval(async () => {
      const s = await api(`/api/download/${r.job_id}`);
      const p = s.progress;
      const pct = p.total > 0 ? Math.round(p.ok / p.total * 100) : 0;
      document.getElementById('reencode-fill').style.width = pct + '%';
      document.getElementById('reencode-text').textContent = `${p.ok} / ${p.total} files${p.failed ? ', ' + p.failed + ' failed' : ''}`;
      if (s.done) {
        clearInterval(iv);
        document.getElementById('reencode-text').textContent = '✅ Done';
        toast('Re-encode complete');
      }
    }, 1000);
  } catch (e) {
    document.getElementById('reencode-text').textContent = 'Error: ' + e.message;
  }
}

// ---- Maintenance ----

async function scanLibrary() {
  const el = document.getElementById('maint-result');
  el.textContent = 'Scanning...';
  try {
    const r = await api('/api/library/scan', { method: 'POST' });
    el.textContent = `✅ Added ${r.added} tracks`;
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}

async function importLog() {
  const el = document.getElementById('maint-result');
  el.textContent = 'Importing...';
  try {
    const r = await api('/api/library/import-log', { method: 'POST' });
    el.textContent = `✅ Added ${r.added} failed tracks from log`;
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}

async function retryFailed() {
  const el = document.getElementById('maint-result');
  el.textContent = 'Starting retry...';
  try {
    const r = await api('/api/library/retry-failed', { method: 'POST' });
    if (r.total === 0) {
      el.textContent = 'No failed tracks to retry';
    } else {
      el.textContent = `🔄 Retrying ${r.total} tracks in background`;
    }
  } catch (e) { el.textContent = 'Error: ' + e.message; }
}

loadSettings();
checkCloud();

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

