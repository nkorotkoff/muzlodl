
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
    toast(t('import.toast.parseError', {msg: err.message}), true);
    return;
  }
  if (parsed.length) {
    parsed.forEach(t => pendingTracks.push(t));
    renderTrackList();
    toast(t('import.toast.added', {n: parsed.length, name: file.name}));
  } else {
    toast(t('import.toast.noTracks'), true);
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
  if (!title) { toast(t('import.toast.titleRequired'), true); return; }
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
    if (dlBtn) { dlBtn.disabled = true; dlBtn.textContent = t('import.download'); dlBtn.title = t('import.downloadAddFirst'); }
    return;
  }
  box.style.display = 'block';
  if (dlBtn) { dlBtn.disabled = false; dlBtn.textContent = t('import.downloadCount', {n: pendingTracks.length}); dlBtn.title = ''; }
  const noArtist = pendingTracks.filter(t => !t.artist);
  document.getElementById('status-heading').textContent =
    t('import.trackList', {n: pendingTracks.length})
    + (noArtist.length ? ' — ' + t('import.noArtistWarn', {n: noArtist.length}) : '');
  tbody.innerHTML = pendingTracks.map((t, i) => {
    const artistCell = t.artist
      ? escape(t.artist)
      : '<span class="no-artist">' + t('import.noArtist') + '</span>';
    const badge = !t.artist
      ? '<span class="badge badge-failed">' + t('import.wontDownload') + '</span>'
      : '<span class="badge badge-pending">' + t('import.pending') + '</span>';
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
  if (!pendingTracks.length) { toast(t('import.toast.addFirst'), true); return; }
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
    toast(t('common.error', {msg: e.message}), true);
    prog.style.display = 'none';
  }
}

// ---- Track status table ----
let currentSession = null;
let trackPollTimer = null;

function escape(s) {
  const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML;
}

// ---- Session track lists: infinite scroll pagination ----
const PAGE_SIZE = 100;
// sid -> {loaded, total, loading}
const sessionState = {};

function sessionListEl(sid) {
  return sid === currentSession
    ? document.getElementById('status-tbody')
    : document.getElementById('session-' + sid);
}

function badgeHTML(status) {
  return status === 'ok'
    ? '<span class="badge badge-ok">' + t('import.status.downloaded') + '</span>'
    : status === 'cached'
      ? '<span class="badge badge-cached">' + t('import.status.cached') + '</span>'
      : status === 'failed'
        ? '<span class="badge badge-failed">' + t('import.status.failed') + '</span>'
        : '<span class="badge badge-pending">' + t('import.status.pending') + '</span>';
}

function trackRowHTML(t, extraCell) {
  const retry = t.status === 'failed'
    ? `<button class="btn-retry" data-id="${t.id}" title="Retry">↻</button>`
    : '';
  return `<tr>
    <td>${escape(t.artist)}</td>
    <td>${escape(t.title)}</td>
    <td>${escape(t.album)}</td>
    <td>${badgeHTML(t.status)} ${retry}</td>${extraCell || ''}
  </tr>`;
}

function tracksTableHTML(tracks) {
  return '<table><thead><tr><th>Artist</th><th>Title</th><th>Album</th><th>Status</th></tr></thead><tbody>'
    + tracks.map(t => trackRowHTML(t)).join('')
    + '</tbody></table>';
}

function sentinelHTML(sid) {
  return `<div class="scroll-sentinel" data-sid="${sid}"></div>`;
}

const scrollIO = ('IntersectionObserver' in window)
  ? new IntersectionObserver((entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const el = en.target;
        if (!el.classList.contains('active')) continue;
        loadMoreTracks(Number(el.dataset.sid));
      }
    }, { rootMargin: '300px' })
  : null;

if (!scrollIO) {
  window.addEventListener('scroll', () => {
    document.querySelectorAll('.scroll-sentinel.active').forEach(s => {
      if (s.getBoundingClientRect().top < innerHeight + 300) {
        loadMoreTracks(Number(s.dataset.sid));
      }
    });
  }, { passive: true });
}

function updateSentinel(sid) {
  const st = sessionState[sid];
  const s = document.querySelector(`.scroll-sentinel[data-sid="${sid}"]`);
  if (!s || !st) return;
  s.classList.remove('active');
  if (st.loading) {
    s.textContent = t('infinite.loading');
    s.classList.add('active');
  } else if (st.total > st.loaded) {
    s.textContent = t('infinite.more', {remaining: st.total - st.loaded, total: st.total});
    s.classList.add('active');
  } else if (st.total > PAGE_SIZE) {
    s.textContent = t('infinite.allShown', {total: st.total});
  } else {
    s.textContent = '';
  }
  if (scrollIO && s.dataset.watched !== '1') {
    s.dataset.watched = '1';
    scrollIO.observe(s);
  }
}

async function loadMoreTracks(sid) {
  const st = sessionState[sid];
  if (!st || st.loading || st.loaded >= st.total) return;
  st.loading = true;
  updateSentinel(sid);
  try {
    const r = await fetch(`/api/imports/${sid}?limit=${PAGE_SIZE}&offset=${st.loaded}`);
    if (r.ok) {
      const d = await r.json();
      const rows = d.tracks || [];
      st.total = d.total || rows.length;
      if (rows.length) {
        const el = sessionListEl(sid);
        if (el) {
          const html = rows.map(t =>
            sid === currentSession ? trackRowHTML(t, '<td></td>') : trackRowHTML(t)
          ).join('');
          if (el.tagName === 'TBODY') {
            el.insertAdjacentHTML('beforeend', html);
          } else {
            const tb = el.querySelector('tbody');
            if (tb) tb.insertAdjacentHTML('beforeend', html);
          }
          st.loaded += rows.length;
        }
      }
    }
  } catch (e) { /* ignore */ }
  st.loading = false;
  updateSentinel(sid);
}

// Full re-render of everything loaded so far (used by status polling).
async function refreshSessionTracks(sid) {
  try {
    let st = sessionState[sid];
    if (!st) { st = sessionState[sid] = { loaded: 0, total: 0 }; }
    const limit = Math.min(Math.max(st.loaded, PAGE_SIZE), 500);
    const r = await fetch(`/api/imports/${sid}?limit=${limit}&offset=0`);
    if (!r.ok) return null;
    const d = await r.json();
    const tracks = d.tracks || [];
    st.total = d.total;
    // We just replaced the whole list — synced count must match the DOM
    // (the API caps limit at 500, so deep scroll positions reset cleanly).
    st.loaded = tracks.length;
    renderSessionTracks(sid, tracks);
    updateSentinel(sid);
    return d;
  } catch (e) { return null; }
}

function renderSessionTracks(sid, tracks) {
  const el = sessionListEl(sid);
  if (!el) return;
  if (sid === currentSession && el.tagName === 'TBODY') {
    el.innerHTML = tracks.map(t => trackRowHTML(t, '<td></td>')).join('');
    const host = document.getElementById('status-pager');
    if (host) host.innerHTML = sentinelHTML(sid);
  } else {
    el.innerHTML = tracksTableHTML(tracks) + sentinelHTML(sid);
  }
}

// Retry buttons are delegated — safe across innerHTML re-renders and appends.
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.btn-retry');
  if (!btn) return;
  btn.textContent = '…';
  try { await fetch(`/api/library/${btn.dataset.id}/retry`, { method: 'POST' }); } catch (err) {}
  btn.textContent = '↻';
});

function pollTracks(sessionId) {
  sessionState[sessionId] = { loaded: 0, total: 0 };
  if (trackPollTimer) clearInterval(trackPollTimer);
  trackPollTimer = setInterval(async () => {
    const d = await refreshSessionTracks(sessionId);
    if (!d) return;
    // Stop once every track in the session reached a final state
    // (use session counters — visible rows may not cover all tracks)
    const s = d.session || {};
    if ((s.downloaded || 0) + (s.failed || 0) >= (s.total || 0)) {
      clearInterval(trackPollTimer);
      trackPollTimer = null;
    }
  }, 1500);
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
        t('import.progressOkFailed', {ok: p.ok, failed: p.failed, remaining: total - done});
      if (s.done || s.cancelled) {
        clearInterval(interval);
        if (s.cancelled) {
          document.getElementById('progress-text').textContent = t('common.cancelled');
        } else {
          document.getElementById('progress-text').textContent = t('common.complete');
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
    el.innerHTML = '<p class="hint">' + (q ? t('import.noMatchFilter') : t('import.noImports')) + '</p>';
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
    more.innerHTML = '<button onclick="recentShown += 20; applyRecentFilter()">' + t('import.showMore', {n: filtered.length - shown.length}) + '</button>';
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
  if (!sessionState[sid]) sessionState[sid] = { loaded: 0, total: 0 };
  const poll = async () => {
    const d = await refreshSessionTracks(sid);
    if (!d) return;
    const body = document.getElementById('session-' + sid);
    if (!body) { stopSessionPoll(sid); return; }
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
  };
  poll();
  recentTimers[sid] = setInterval(poll, 1500);
}

function stopSessionPoll(sid) {
  if (recentTimers[sid]) { clearInterval(recentTimers[sid]); delete recentTimers[sid]; }
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
    toast(t('common.cancelled'));
  } catch (e) { toast(t('common.error', {msg: e.message}), true); }
}

window.addEventListener('langchange', function() { applyI18n(); renderTrackList(); applyRecentFilter(); updateSentinel(currentSession); });

