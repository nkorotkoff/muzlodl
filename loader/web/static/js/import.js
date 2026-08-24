
let currentJob = null;

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) { const e = await r.json().catch(() => ({error: r.statusText})); throw new Error(e.error || r.statusText); }
  return r.json();
}

function toast(msg, err = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = err ? 'toast-error' : 'toast-info';
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

document.getElementById('file-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const text = await file.text();
  const name = file.name.toLowerCase();
  const type = name.endsWith('.csv') ? 'csv' : name.endsWith('.json') ? 'json' : 'text';
  let parsed;
  try {
    parsed = type === 'csv' ? parseCSV(text)
      : type === 'json' ? parseJSON(text)
      : parseText(text);
  } catch (err) {
    toast('Parse error: ' + err.message, true);
    return;
  }
  if (parsed.length) {
    parsed.forEach(t => pendingTracks.push(t));
    renderTrackList();
    toast(`Added ${parsed.length} tracks from ${file.name}`);
  } else {
    toast('No tracks found in file', true);
  }
});

// CSV: header row artist,title,album[,year,...]
function parseCSV(content) {
  const lines = content.split('\n').map(l => l.trim()).filter(Boolean);
  if (!lines.length) return [];
  const header = lines[0].split(',').map(h => h.trim().toLowerCase());
  const aIdx = header.indexOf('artist'), tIdx = header.indexOf('title');
  if (tIdx < 0) throw new Error('CSV needs a title column');
  const out = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(',');
    out.push({
      artist: aIdx >= 0 ? (cells[aIdx] || '').trim() : '',
      title: (cells[tIdx] || '').trim(),
      album: header.indexOf('album') >= 0 ? (cells[header.indexOf('album')] || '').trim() : '',
    });
  }
  return out.filter(t => t.title);
}

// JSON: array of {artist,title,album} or {tracks:[...]}
function parseJSON(content) {
  const data = JSON.parse(content);
  const arr = Array.isArray(data) ? data : (data.tracks || data.items || data.songs || []);
  if (!Array.isArray(arr)) throw new Error('JSON must be an array or {tracks:[...]}');
  return arr.map(t => ({
    artist: (t.artist || '').trim(),
    title: (t.title || '').trim(),
    album: (t.album || '').trim(),
  })).filter(t => t.title);
}

// ---- Track collection ----
const PENDING_KEY = 'music-loader-pending';
let pendingTracks = [];

function loadPending() {
  try {
    const saved = JSON.parse(localStorage.getItem(PENDING_KEY) || '[]');
    if (Array.isArray(saved)) pendingTracks = saved;
  } catch (e) {}
}

function savePending() {
  try { localStorage.setItem(PENDING_KEY, JSON.stringify(pendingTracks)); } catch (e) {}
}

function addTrack() {
  const artist = document.getElementById('input-artist').value.trim();
  const title = document.getElementById('input-title').value.trim();
  if (!title) { toast('Title is required', true); return; }
  pendingTracks.push({ artist, title, album: '' });
  savePending();
  document.getElementById('input-title').value = '';
  renderTrackList();
  document.getElementById('input-title').focus();
}

function removeTrack(idx) {
  pendingTracks.splice(idx, 1);
  savePending();
  renderTrackList();
}

// Enter key adds track
['input-artist', 'input-title'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addTrack(); }
  });
});

function renderTrackList() {
  const box = document.getElementById('track-status');
  const tbody = document.getElementById('status-tbody');
  const dlBtn = document.getElementById('download-btn');
  if (!pendingTracks.length) {
    box.style.display = 'none';
    if (dlBtn) { dlBtn.disabled = true; dlBtn.textContent = '▶ Download'; dlBtn.title = 'Add tracks first'; }
    return;
  }
  box.style.display = 'block';
  if (dlBtn) { dlBtn.disabled = false; dlBtn.textContent = '▶ Download (' + pendingTracks.length + ')'; dlBtn.title = ''; }
  const noArtist = pendingTracks.filter(t => !t.artist);
  document.getElementById('status-heading').textContent =
    'Track list (' + pendingTracks.length + ')'
    + (noArtist.length ? ' — ⚠ ' + noArtist.length + ' без артиста' : '');
  tbody.innerHTML = pendingTracks.map((t, i) => {
    const artistCell = t.artist
      ? escape(t.artist)
      : '<span class="no-artist">⚠ нет артиста</span>';
    const badge = !t.artist
      ? '<span class="badge badge-failed" title="Формат: Artist - Title">⚠ не скачается</span>'
      : '<span class="badge badge-pending">⏳ not started</span>';
    return '<tr>'
      + '<td>' + artistCell + '</td>'
      + '<td>' + escape(t.title || '') + '</td>'
      + '<td>' + escape(t.album || '') + '</td>'
      + '<td>' + badge + '</td>'
      + '<td><button class="btn-del" onclick="removeTrack(' + i + ')" title="Remove">✕</button></td>'
      + '</tr>';
  }).join('');
}

function parseText(content) {
  const lines = content.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#'));
  const tracks = [];
  for (const line of lines) {
    if (/^https?:\/\//.test(line)) {
      tracks.push({artist: '', title: line, album: ''});
      continue;
    }
    const parts = line.split(/\s+-\s+/);
    if (parts.length >= 2) {
      tracks.push({artist: parts[0], title: parts[1], album: parts.slice(2).join(' - ')});
    } else {
      tracks.push({artist: '', title: line, album: ''});
    }
  }
  return tracks;
}

function gatherOptions() {
  return {
    quality: document.getElementById('opt-quality').value,
    parallel: parseInt(document.getElementById('opt-parallel').value),
    max_path_len: parseInt(document.getElementById('opt-maxpath').value),
    enrich: document.getElementById('opt-enrich').checked,
    name: document.getElementById('opt-name').value,
  };
}

// Pre-fill download options from saved settings (defaults)
async function loadDefaults() {
  try {
    const s = await api('/api/settings');
    if (s.quality) document.getElementById('opt-quality').value = s.quality;
    if (s.parallel) document.getElementById('opt-parallel').value = s.parallel;
    if (s.max_path_len) document.getElementById('opt-maxpath').value = s.max_path_len;
    if (s.enrich === 'false') document.getElementById('opt-enrich').checked = false;
  } catch (e) { /* use built-in defaults */ }
}
loadDefaults();
loadPending();
renderTrackList();

async function startDownload() {
  if (!pendingTracks.length) { toast('Add tracks first', true); return; }
  // Send structured tracks — no text round-trip, so titles/albums
  // containing " - " survive intact.
  const tracks = pendingTracks.map(t => ({
    artist: t.artist || '',
    title: t.title || '',
    album: t.album || '',
  }));

  const prog = document.getElementById('progress');
  prog.style.display = 'block';
  document.getElementById('progress-fill').style.width = '0%';
  document.getElementById('progress-text').textContent = 'Starting...';

  try {
    const resp = await api('/api/download', {
      method: 'POST',
      body: JSON.stringify({
        source: 'text',
        tracks,
        options: gatherOptions(),
      }),
    });

    currentJob = resp.job_id;
    currentSession = resp.session_id;
    // The download is now owned by the backend session — clear the local draft
    pendingTracks = [];
    savePending();
    renderTrackList();
    document.getElementById('track-status').style.display = 'block';
    pollJob(resp.job_id, resp.total);
    pollTracks(resp.session_id);
  } catch (e) {
    toast('Error: ' + e.message, true);
    prog.style.display = 'none';
  }
}

// ---- Track status table ----
let currentSession = null;
let trackPollTimer = null;

function escape(s) {
  const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML;
}

function pollTracks(sessionId) {
  if (trackPollTimer) clearInterval(trackPollTimer);
  trackPollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/imports/${sessionId}`);
      if (!r.ok) return;
      const d = await r.json();
      renderStatusTable(d.tracks || []);
      // Stop polling once every track reached a final state
      const tracks = d.tracks || [];
      const final = t => t.status === 'ok' || t.status === 'cached' || t.status === 'failed';
      if (tracks.length && tracks.every(final)) {
        clearInterval(trackPollTimer);
        trackPollTimer = null;
      }
    } catch (e) { /* ignore */ }
  }, 1500);
}

function renderStatusTable(tracks) {
  const tbody = document.getElementById('status-tbody');
  tbody.innerHTML = tracks.map(t => {
    const badge = t.status === 'ok'
      ? '<span class="badge badge-ok">✅ downloaded</span>'
      : t.status === 'cached'
        ? '<span class="badge badge-cached">↺ already have</span>'
        : t.status === 'failed'
          ? '<span class="badge badge-failed">❌ failed</span>'
          : '<span class="badge badge-pending">⏳ pending</span>';
    const retry = t.status === 'failed'
      ? `<button class="btn-retry" data-id="${t.id}" title="Retry">↻</button>`
      : '';
    return `<tr>
      <td>${escape(t.artist)}</td>
      <td>${escape(t.title)}</td>
      <td>${escape(t.album)}</td>
      <td>${badge} ${retry}</td>
      <td></td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('.btn-retry').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.textContent = '…';
      try {
        await fetch(`/api/library/${btn.dataset.id}/retry`, { method: 'POST' });
      } catch (e) {}
      btn.textContent = '↻';
    });
  });
}

function pollJob(jobId, total) {
  const interval = setInterval(async () => {
    try {
      const s = await api(`/api/download/${jobId}`);
      const p = s.progress;
      const done = p.ok + p.failed;
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;
      document.getElementById('progress-fill').style.width = Math.min(pct, 100) + '%';
      document.getElementById('progress-text').textContent =
        `${p.ok} ok, ${p.failed} failed, ${total - done} remaining`;
      if (s.done || s.cancelled) {
        clearInterval(interval);
        if (s.cancelled) {
          document.getElementById('progress-text').textContent = 'Cancelled';
        } else {
          document.getElementById('progress-text').textContent = '✅ Complete!';
        }
        currentJob = null;
      }
    } catch (e) {
      clearInterval(interval);
    }
  }, 1000);
}

// ---- Recent imports (persist across reloads) ----
let recentTimers = {};
let openSessions = new Set();

async function loadRecentImports() {
  try {
    const r = await fetch('/api/imports?limit=8');
    if (!r.ok) return;
    const d = await r.json();
    renderRecentImports(d.sessions || []);
  } catch (e) { /* ignore */ }
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
}

function renderRecentImports(sessions) {
  // Store the full list, render only what's visible (filter + limit)
  allSessions = sessions;
  applyRecentFilter();
}

let allSessions = [];
let recentShown = 20;

function applyRecentFilter() {
  const el = document.getElementById('recent-list');
  const filter = (document.getElementById('recent-filter') || {}).value || '';
  const q = filter.trim().toLowerCase();
  const filtered = q
    ? allSessions.filter(s => (s.source_name || s.source || '').toLowerCase().includes(q))
    : allSessions;

  const shown = filtered.slice(0, recentShown);
  if (!shown.length) {
    el.innerHTML = '<p class="hint">' + (q ? 'Nothing matches the filter' : 'No imports yet') + '</p>';
    return;
  }

  el.innerHTML = shown.map(s => {
    const done = s.downloaded + s.failed;
    const pct = s.total > 0 ? Math.round(done / s.total * 100) : 0;
    const label = s.source_name || s.source || 'Import';
    const isOpen = openSessions.has(s.id);
    return `<div class="recent-item" data-sid="${s.id}">
      <div class="recent-row" onclick="toggleSession(${s.id})">
        <span class="recent-label">${escape(label)}</span>
        <span class="recent-meta">${fmtTime(s.created_at)} · ${done}/${s.total} (${pct}%)</span>
        <span class="recent-arrow" id="arrow-${s.id}">${isOpen ? '▾' : '▸'}</span>
      </div>
      <div class="recent-body" id="session-${s.id}" style="display:${isOpen ? 'block' : 'none'}"></div>
    </div>`;
  }).join('');

  // "Show more" button when there are hidden sessions
  if (shown.length < filtered.length) {
    const more = document.createElement('div');
    more.style.textAlign = 'center';
    more.style.margin = '.5rem 0';
    more.innerHTML = `<button onclick="recentShown += 20; applyRecentFilter()">Show more (${filtered.length - shown.length} more)</button>`;
    el.appendChild(more);
  }

  // Re-open sessions that were open before the re-render
  openSessions.forEach(sid => {
    const body = document.getElementById('session-' + sid);
    if (body) {
      body.style.display = 'block';
      // Only poll sessions that aren't finished yet
      const s = allSessions.find(x => x.id === sid);
      if (s && s.downloaded + s.failed < s.total) {
        startSessionPoll(sid);
      }
    }
  });

  // Auto-expand the most recent session if it's still running
  if (shown.length) {
    const latest = shown[0];
    if (latest.total > latest.downloaded + latest.failed && !openSessions.has(latest.id)) {
      openSessions.add(latest.id);
      toggleSession(latest.id, true);
    }
  }
}

function toggleSession(sid, force) {
  const body = document.getElementById('session-' + sid);
  const arrow = document.getElementById('arrow-' + sid);
  const willShow = force ? true : body.style.display === 'none';
  if (willShow) {
    openSessions.add(sid);
    body.style.display = 'block';
    if (arrow) arrow.textContent = '▾';
    startSessionPoll(sid);
  } else {
    openSessions.delete(sid);
    body.style.display = 'none';
    if (arrow) arrow.textContent = '▸';
    stopSessionPoll(sid);
  }
}

function startSessionPoll(sid) {
  if (recentTimers[sid]) clearInterval(recentTimers[sid]);
  const poll = async () => {
    try {
      const r = await fetch(`/api/imports/${sid}`);
      if (!r.ok) return;
      const d = await r.json();
      const body = document.getElementById('session-' + sid);
      if (!body) { stopSessionPoll(sid); return; }
      body.innerHTML = renderTracksTable(d.tracks || []);
      // Re-bind retry buttons
      body.querySelectorAll('.btn-retry').forEach(btn => {
        btn.addEventListener('click', async () => {
          btn.textContent = '…';
          try { await fetch(`/api/library/${btn.dataset.id}/retry`, { method: 'POST' }); } catch (e) {}
          btn.textContent = '↻';
        });
      });
      // Stop when session fully done — update counters in place, don't
      // re-render the whole list (that would re-trigger polls → loop).
      if (d.session && d.session.downloaded + d.session.failed >= d.session.total) {
        stopSessionPoll(sid);
        const row = document.querySelector(`.recent-item[data-sid="${sid}"] .recent-meta`);
        if (row) {
          const s = d.session;
          row.textContent = fmtTime(s.created_at) + ' · ' + (s.downloaded + s.failed) + '/' + s.total + ' (' + Math.round((s.downloaded + s.failed) / s.total * 100) + '%)';
        }
      }
    } catch (e) { /* ignore */ }
  };
  poll();
  recentTimers[sid] = setInterval(poll, 1500);
}

function stopSessionPoll(sid) {
  if (recentTimers[sid]) { clearInterval(recentTimers[sid]); delete recentTimers[sid]; }
}

function renderTracksTable(tracks) {
  return '<table><thead><tr><th>Artist</th><th>Title</th><th>Album</th><th>Status</th></tr></thead><tbody>'
    + tracks.map(t => {
      const badge = t.status === 'ok'
        ? '<span class="badge badge-ok">✅ downloaded</span>'
        : t.status === 'cached'
          ? '<span class="badge badge-cached">↺ already have</span>'
          : t.status === 'failed'
            ? '<span class="badge badge-failed">❌ failed</span>'
            : '<span class="badge badge-pending">⏳ pending</span>';
      const retry = t.status === 'failed'
        ? `<button class="btn-retry" data-id="${t.id}" title="Retry">↻</button>`
        : '';
      return `<tr>
        <td>${escape(t.artist)}</td>
        <td>${escape(t.title)}</td>
        <td>${escape(t.album)}</td>
        <td>${badge} ${retry}</td>
      </tr>`;
    }).join('')
    + '</tbody></table>';
}

// Load recent imports on page load
loadRecentImports();

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// Recent imports filter
const recentFilter = document.getElementById('recent-filter');
if (recentFilter) {
  recentFilter.addEventListener('input', debounce(() => {
    recentShown = 20;
    applyRecentFilter();
  }, 300));
}

async function cancelDownload() {
  if (!currentJob) return;
  try {
    await api(`/api/download/${currentJob}`, { method: 'DELETE' });
    toast('Cancelled');
  } catch (e) { toast('Error: ' + e.message, true); }
}

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

