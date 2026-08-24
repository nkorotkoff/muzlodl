
const audio = document.getElementById('audio');
const player = document.getElementById('player');
const mainEl = document.getElementById('app');

// ---- Play queue (grows dynamically beyond current page) ----
let queue = [];           // [{id, artist, title}] — ordered play list
let queueIndex = -1;      // current position in queue
let shuffleMode = false;  // shuffle the whole library
let shuffleAllIds = [];   // full shuffled ID array
let shufflePos = 0;       // position in shuffleAllIds

// ---- State ----
let state = {
  tracks: [], total: 0, offset: 0, limit: 100,
  query: '', status: '', sort: 'artist', order: 'asc',
};

// ---- Filter persistence (survives page reloads) ----
const FILTERS_KEY = 'library-filters';
function saveFilters() {
  try {
    localStorage.setItem(FILTERS_KEY, JSON.stringify({
      query: state.query, sort: state.sort, order: state.order,
    }));
  } catch (e) {}
}
function restoreFilters() {
  try {
    const f = JSON.parse(localStorage.getItem(FILTERS_KEY) || 'null');
    if (!f) return;
    if (typeof f.query === 'string') state.query = f.query;
    if (typeof f.sort === 'string') state.sort = f.sort;
    if (typeof f.order === 'string') state.order = f.order;
  } catch (e) {}
}

// ---- API ----
async function api(path, opts) {
  const r = await fetch(path, {
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) { const e = await r.json().catch(() => ({error: r.statusText})); throw new Error(e.error || r.statusText); }
  return r.json();
}

async function fetchTracks(params) {
  const q = new URLSearchParams(params);
  return api('/api/library?' + q.toString());
}

// ---- Queue management ----
async function ensureQueueHas(trackId) {
  // If track already in queue, find its index
  let idx = queue.findIndex(t => t.id === trackId);
  if (idx >= 0) { queueIndex = idx; updateQueueInfo(); return; }

  // Build the queue from the ENTIRE library (all pages of current filter),
  // not just the currently loaded page — so the counter shows the real
  // position among all tracks.
  const query = state.query;
  const status = state.status;
  const sort = state.sort;

  const all = [];
  let off = 0;
  while (true) {
    const page = await fetchTracks({ q: query, status, sort, limit: 500, offset: off });
    all.push(...page.tracks.filter(t => t.file_path));
    if (page.tracks.length < 500 || all.length >= page.total) break;
    off += 500;
  }

  queue = all.map(t => ({ id: t.id, artist: t.artist, title: t.title }));
  queueIndex = queue.findIndex(t => t.id === trackId);
  if (queueIndex === -1) queueIndex = 0;
  updateQueueInfo();
}

function updateQueueInfo() {
  const el = document.getElementById('player-queue-info');
  if (shuffleMode) {
    const remaining = shuffleAllIds.length - shufflePos;
    el.textContent = `${remaining} in shuffle queue`;
  } else {
    el.textContent = `${queueIndex + 1} / ${queue.length}`;
  }
}

// ---- Shuffle (across entire library) ----
async function toggleShuffle() {
  shuffleMode = !shuffleMode;
  const btn = document.getElementById('shuffle-btn');
  btn.style.opacity = shuffleMode ? '1' : '0.5';
  btn.textContent = shuffleMode ? '🔀' : '🔀';

  if (shuffleMode && currentTrackId) {
    // Fetch ALL downloadable track IDs and shuffle
    await rebuildShuffle(currentTrackId);
  }
  updateQueueInfo();
}

async function rebuildShuffle(currentTrackId) {
  // Fetch all tracks with file_path in batches to get IDs
  const allTracks = [];
  const params = { q: state.query, status: state.status, sort: 'artist', limit: 200 };
  const first = await fetchTracks({ ...params, offset: 0 });
  allTracks.push(...first.tracks.filter(t => t.file_path));
  for (let off = 200; off < first.total; off += 200) {
    const page = await fetchTracks({ ...params, offset: off });
    allTracks.push(...page.tracks.filter(t => t.file_path));
    if (page.tracks.length < 200) break;
  }

  // Shuffle all
  shuffleAllIds = allTracks.map(t => ({ id: t.id, artist: t.artist, title: t.title }));
  for (let i = shuffleAllIds.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffleAllIds[i], shuffleAllIds[j]] = [shuffleAllIds[j], shuffleAllIds[i]];
  }

  // Place current track at the top
  const curIdx = shuffleAllIds.findIndex(t => t.id === currentTrackId);
  if (curIdx > 0) {
    [shuffleAllIds[0], shuffleAllIds[curIdx]] = [shuffleAllIds[curIdx], shuffleAllIds[0]];
  }
  shufflePos = 0;
}

function getShuffleNext() {
  shufflePos++;
  if (shufflePos >= shuffleAllIds.length) shufflePos = 0;
  return shuffleAllIds[shufflePos];
}

function getShufflePrev() {
  shufflePos--;
  if (shufflePos < 0) shufflePos = shuffleAllIds.length - 1;
  return shuffleAllIds[shufflePos];
}

// ---- Player ----
let currentTrackId = null;

function playTrack(id, artist, title) {
  if (currentTrackId === id && !audio.paused) { togglePlay(); return; }

  // If shuffle is active: play the clicked track immediately, position it
  // in the shuffled list. Rebuild only if the shuffle list isn't built yet
  // (first play after enabling shuffle).
  if (shuffleMode) {
    if (!shuffleAllIds.length) {
      rebuildShuffle(id).then(() => {
        currentTrackId = id;
        doPlay(id, artist, title);
      });
      return;
    }
    // Already have the shuffled list — just play, keep current position
    const pos = shuffleAllIds.findIndex(t => t.id === id);
    if (pos >= 0) shufflePos = pos;
    currentTrackId = id;
    doPlay(id, artist, title);
    return;
  }

  // Build queue from current view first
  if (currentTrackId !== id || queue.length === 0) {
    ensureQueueHas(id).then(() => {
      currentTrackId = id;
      doPlay(id, artist, title);
    });
    return;
  }

  currentTrackId = id;
  doPlay(id, artist, title);
}

function doPlay(id, artist, title) {
  playRecorded = false;  // new track = new listen
  document.getElementById('player-artist').textContent = artist;
  document.getElementById('player-title').textContent = title;
  audio.src = `/api/library/${id}/stream`;
  player.style.display = 'block';
  mainEl.classList.add('has-player');
  audio.load();
  audio.play().then(() => {
    document.getElementById('play-btn').textContent = '⏸';
  }).catch(() => {});
  updateQueueInfo();
  renderTracks();
  // Reset saved position on track change, save new track immediately
  if (saveTimer) clearTimeout(saveTimer);
  try { localStorage.setItem(STATE_KEY, JSON.stringify({
    id, artist, title, currentTime: 0, volume: audio.volume, playing: true,
  })); } catch (e) {}
}

function nextTrack() {
  if (shuffleMode) {
    if (!shuffleAllIds.length) return;
    const next = getShuffleNext();
    playTrack(next.id, next.artist, next.title);
    return;
  }
  if (!queue.length) return;
  queueIndex = (queueIndex + 1) % queue.length;
  const t = queue[queueIndex];
  playTrack(t.id, t.artist, t.title);
}

function prevTrack() {
  if (shuffleMode) {
    if (!shuffleAllIds.length) return;
    const prev = getShufflePrev();
    playTrack(prev.id, prev.artist, prev.title);
    return;
  }
  if (!queue.length) return;
  if (audio.currentTime > 3) { audio.currentTime = 0; return; }
  queueIndex = (queueIndex - 1 + queue.length) % queue.length;
  const t = queue[queueIndex];
  playTrack(t.id, t.artist, t.title);
}

function togglePlay() {
  if (audio.paused) {
    audio.play().then(() => document.getElementById('play-btn').textContent = '⏸');
  } else {
    audio.pause();
    document.getElementById('play-btn').textContent = '▶';
  }
}

function closePlayer() {
  audio.pause(); audio.src = '';
  player.style.display = 'none';
  mainEl.classList.remove('has-player');
  currentTrackId = null; queue = []; queueIndex = -1;
  shuffleMode = false; shuffleAllIds = []; shufflePos = 0;
  document.getElementById('shuffle-btn').style.opacity = '0.5';
  renderTracks();
  try { localStorage.removeItem(STATE_KEY); } catch (e) {}
}

function seek(e) {
  const rect = e.currentTarget.getBoundingClientRect();
  const pct = (e.clientX - rect.left) / rect.width;
  if (audio.duration) audio.currentTime = pct * audio.duration;
}

// Volume
document.getElementById('volume-slider').addEventListener('input', function() {
  audio.volume = this.value;
  const icon = this.closest('.player-volume').querySelector('.volume-icon');
  icon.textContent = this.value == 0 ? '🔇' : this.value < 0.4 ? '🔉' : '🔊';
});

// Audio events
let playRecorded = false;  // one listen per track play (reset in doPlay)

audio.addEventListener('timeupdate', () => {
  if (audio.duration) {
    document.getElementById('progress-fill').style.width = (audio.currentTime / audio.duration * 100) + '%';
    document.getElementById('player-current').textContent = fmtTime(audio.currentTime);
  }
  // Record the listen once we've actually listened to a meaningful chunk
  // (>=30s and >=30% of the track) — not only when the track ends. Testing
  // and skips count too.
  if (!playRecorded && currentTrackId && audio.currentTime >= 30 &&
      audio.duration && audio.currentTime >= audio.duration * 0.3) {
    playRecorded = true;
    fetch('/api/plays', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({track_id: currentTrackId}),
    }).catch(() => {});
  }
});
audio.addEventListener('loadedmetadata', () => {
  if (audio.duration && isFinite(audio.duration))
    document.getElementById('player-duration').textContent = fmtTime(audio.duration);
});
audio.addEventListener('ended', () => {
  // Listen already recorded in timeupdate once the 30s/30% threshold was
  // crossed; nothing extra to do here.
  if (shuffleMode) { nextTrack(); return; }
  if (queue.length > 1) { nextTrack(); return; }
  document.getElementById('play-btn').textContent = '▶';
  document.getElementById('progress-fill').style.width = '0%';
});

function fmtTime(sec) {
  if (!sec || !isFinite(sec)) return '0:00';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m + ':' + (s < 10 ? '0' : '') + s;
}

// ---- Render ----
function fmtSize(b) {
  if (!b) return '—';
  if (b > 1e9) return (b/1e9).toFixed(1)+'GB';
  if (b > 1e6) return (b/1e6).toFixed(1)+'MB';
  return (b/1e3).toFixed(0)+'KB';
}
function fmtDur(sec) {
  if (!sec) return '—';
  var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m + ':' + (s < 10 ? '0' : '') + s;
}

function exportLibrary(fmt) {
  window.location = '/api/library/export?format=' + fmt;
}
function escape(s) {
  const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML;
}

function renderTracks() {
  const tbody = document.getElementById('tbody');
  if (!state.tracks.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No tracks found</td></tr>';
    document.getElementById('count').textContent = '0 / 0';
    return;
  }
  tbody.innerHTML = state.tracks.map(t => {
    const isPlaying = currentTrackId === t.id;
    const canPlay = !!t.file_path;
    const rowClass = isPlaying ? ' class="row-playing"' : '';
    const playAttr = canPlay ? ' data-id="' + t.id + '" data-artist="' + t.artist.replace(/"/g,'&quot;') + '" data-title="' + t.title.replace(/"/g,'&quot;') + '"' : '';
    const dur = t.duration ? fmtDur(t.duration) : '—';
    return '<tr' + rowClass + '>'
      + '<td>' + (canPlay
        ? '<button class="btn-play' + (isPlaying ? ' playing' : '') + '" data-play="1"' + playAttr + ' title="Play">' + (isPlaying ? '⏸' : '▶') + '</button>'
        : '<span class="no-play">—</span>') + '</td>'
      + '<td><input type="checkbox" class="row-cb" value="' + t.id + '"></td>'
      + '<td>' + escape(t.artist) + '</td>'
      + '<td><strong>' + escape(t.title) + '</strong></td>'
      + '<td>' + escape(t.album) + '</td>'
      + '<td>' + dur + '</td>'
      + '<td>' + fmtSize(t.file_size) + '</td>'
      + '<td>' + (canPlay ? '<div class="dd-wrap">'
        + '<button class="btn-dd" data-dd="1" title="Actions">⋮</button>'
        + '<div class="dd-menu">'
        +   '<button class="dd-item" data-dd-dl="' + t.id + '">⬇ Download</button>'
        +   '<button class="dd-item" data-dd-reload="' + t.id + '">🔄 Re-download from another source</button>'
        +   '<button class="dd-item" data-dd-edit="' + t.id + '">✎ Edit</button>'
        +   '<button class="dd-item dd-danger" data-dd-del="' + t.id + '">✕ Delete</button>'
        + '</div></div>' : '') + '</td>'
    + '</tr>';
  }).join('');

  document.getElementById('count').textContent = state.tracks.length + ' / ' + state.total;

  // Delegate play button clicks
  tbody.querySelectorAll('[data-play]').forEach(btn => {
    btn.addEventListener('click', function() {
      playTrack(parseInt(this.dataset.id), this.dataset.artist, this.dataset.title);
    });
  });

  // Delete handlers
  tbody.querySelectorAll('.btn-del').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Delete this track?')) return;
      try {
        await api(`/api/library/${btn.dataset.id}`, { method: 'DELETE' });
        if (currentTrackId == btn.dataset.id) closePlayer();
        toast('Deleted'); loadLibrary();
      } catch (e) { toast('Error: ' + e.message, true); }
    });
  });

  // Edit handlers
  tbody.querySelectorAll('.btn-edit').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openEditModal(parseInt(btn.dataset.id));
    });
  });

  // Download handlers — plain navigation so the file saves to disk
  tbody.querySelectorAll('.btn-dl').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      window.location = `/api/library/${btn.dataset.id}/download`;
    });
  });

  // Per-row action menu (⋮): open/close on click
  tbody.querySelectorAll('.btn-dd').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const wrap = btn.closest('.dd-wrap');
      const wasOpen = wrap && wrap.classList.contains('open');
      closeAllMenus();
      // Toggle: clicking the ⋮ of an open menu closes it
      if (wrap && !wasOpen) wrap.classList.add('open');
    });
  });

  // Menu item handlers — delegated per item so actions work after re-render
  tbody.querySelectorAll('[data-dd-dl]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      closeAllMenus();
      window.location = `/api/library/${btn.dataset.ddDl}/download`;
    });
  });
  tbody.querySelectorAll('[data-dd-reload]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      closeAllMenus();
      const id = parseInt(btn.dataset.ddReload);
      try {
        const d = await api(`/api/library/${id}/reload`, { method: 'POST' });
        toast('Re-download started');
        pollJobs();
      } catch (e) { toast('Error: ' + e.message, true); }
    });
  });
  tbody.querySelectorAll('[data-dd-edit]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      closeAllMenus();
      openEditModal(parseInt(btn.dataset.ddEdit));
    });
  });
  tbody.querySelectorAll('[data-dd-del]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      closeAllMenus();
      if (!confirm('Delete this track?')) return;
      const id = parseInt(btn.dataset.ddDel);
      try {
        await api(`/api/library/${id}`, { method: 'DELETE' });
        if (currentTrackId == id) closePlayer();
        toast('Deleted'); loadLibrary();
      } catch (e) { toast('Error: ' + e.message, true); }
    });
  });

  // Row checkbox handlers
  tbody.querySelectorAll('.row-cb').forEach(cb => {
    cb.addEventListener('change', updateBatchBar);
  });

  // Select-all
  const selAll = document.getElementById('select-all');
  if (selAll) {
    selAll.checked = false;
    selAll.addEventListener('change', () => {
      tbody.querySelectorAll('.row-cb').forEach(cb => cb.checked = selAll.checked);
      updateBatchBar();
    });
  }
}

// Close every open row menu (⋮)
function closeAllMenus() {
  document.querySelectorAll('.dd-wrap.open').forEach(w => w.classList.remove('open'));
}

// ---- Batch operations ----
function getSelectedIds() {
  const ids = [];
  document.querySelectorAll('.row-cb:checked').forEach(cb => ids.push(parseInt(cb.value)));
  return ids;
}

function updateBatchBar() {
  const bar = document.getElementById('batch-bar');
  const ids = getSelectedIds();
  if (ids.length) {
    bar.style.display = 'flex';
    document.getElementById('batch-count').textContent = `${ids.length} selected`;
  } else {
    bar.style.display = 'none';
  }
}

function clearSelection() {
  document.querySelectorAll('.row-cb').forEach(cb => cb.checked = false);
  const selAll = document.getElementById('select-all');
  if (selAll) selAll.checked = false;
  updateBatchBar();
}

async function batchDelete() {
  const ids = getSelectedIds();
  if (!ids.length) return;
  if (!confirm(`Delete ${ids.length} tracks?`)) return;
  try {
    const r = await api('/api/library/batch-delete', {
      method: 'POST', body: JSON.stringify({ ids }),
    });
    toast(`Deleted ${r.removed} tracks`);
    clearSelection();
    loadLibrary();
  } catch (e) { toast('Error: ' + e.message, true); }
}

// ---- Edit modal ----
let editingId = null;

function openEditModal(id) {
  fetch(`/api/library/${id}`).then(r => r.json()).then(t => {
    editingId = id;
    document.getElementById('edit-artist').value = t.artist || '';
    document.getElementById('edit-title').value = t.title || '';
    document.getElementById('edit-album').value = t.album || '';
    document.getElementById('edit-modal').style.display = 'flex';
  });
}

async function saveEdit() {
  const body = {
    artist: document.getElementById('edit-artist').value,
    title: document.getElementById('edit-title').value,
    album: document.getElementById('edit-album').value,
  };
  try {
    await api(`/api/library/${editingId}`, { method: 'PATCH', body: JSON.stringify(body) });
    toast('Track updated');
    closeModal('edit-modal');
    loadLibrary();
  } catch (e) { toast('Error: ' + e.message, true); }
}

function closeModal(id) {
  document.getElementById(id).style.display = 'none';
}

// ---- Upload modal ----
function showUploadModal() {
  document.getElementById('upload-file').value = '';
  document.getElementById('upload-artist').value = '';
  document.getElementById('upload-title').value = '';
  document.getElementById('upload-album').value = '';
  document.getElementById('upload-modal').style.display = 'flex';
}

document.getElementById('upload-file').addEventListener('change', function() {
  const name = this.files[0] ? this.files[0].name : '';
  if (!name) return;
  const stem = name.replace(/\.[^.]+$/, '');
  if (stem.includes(' - ')) {
    const parts = stem.split(' - ');
    if (!document.getElementById('upload-artist').value) {
      document.getElementById('upload-artist').value = parts[0];
    }
    if (!document.getElementById('upload-title').value) {
      document.getElementById('upload-title').value = parts.slice(1).join(' - ');
    }
  } else if (!document.getElementById('upload-title').value) {
    document.getElementById('upload-title').value = stem;
  }
});

async function doUpload() {
  const file = document.getElementById('upload-file').files[0];
  if (!file) { toast('Choose a file', true); return; }
  const form = new FormData();
  form.append('file', file);
  form.append('artist', document.getElementById('upload-artist').value);
  form.append('title', document.getElementById('upload-title').value);
  form.append('album', document.getElementById('upload-album').value);
  try {
    const r = await fetch('/api/library/upload', { method: 'POST', body: form });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'upload failed');
    toast('Uploaded');
    closeModal('upload-modal');
    loadLibrary();
  } catch (e) { toast('Error: ' + e.message, true); }
}

let loadingMore = false;
let hasMore = true;

// ---- Infinite scroll ----
const sentinel = document.getElementById('scroll-sentinel');
const observer = new IntersectionObserver(async (entries) => {
  if (!entries[0].isIntersecting) return;
  if (loadingMore || !hasMore) return;

  loadingMore = true;
  state.offset += state.limit;
  const params = new URLSearchParams({
    q: state.query, status: state.status, offset: state.offset,
    limit: state.limit, sort: state.sort, order: state.order,
  });
  try {
    const data = await api('/api/library?' + params.toString());
    if (!data.tracks.length) { hasMore = false; loadingMore = false; return; }
    state.tracks = state.tracks.concat(data.tracks);
    state.total = data.total;
    hasMore = state.tracks.length < state.total;
    renderTracks();
  } catch (e) { /* ignore */ }
  loadingMore = false;
}, { rootMargin: '300px' });

observer.observe(sentinel);

// ---- Load with pagination reset ----
async function loadLibrary() {
  state.offset = 0;
  hasMore = true;
  loadingMore = false;
  const params = new URLSearchParams({
    q: state.query, status: state.status, offset: 0,
    limit: state.limit, sort: state.sort, order: state.order,
  });
  const data = await api('/api/library?' + params.toString());
  state.tracks = data.tracks;
  state.total = data.total;
  hasMore = data.tracks.length < data.total;
  renderTracks();
}

function resetInfiniteScroll() {
  hasMore = true;
  loadingMore = false;
}

async function loadStats() {
  try {
    const d = await api('/api/disk');
    document.getElementById('stats').textContent = `${d.files} files · ${d.size_human} · ${d.artists} artists`;
  } catch (_) {}
}

function toast(msg, err) {
  const el = document.getElementById('toast');
  el.textContent = msg; el.className = err ? 'toast-error' : 'toast-info';
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 3000);
}

// ---- Events ----
document.getElementById('search').addEventListener('input', debounce(() => {
  state.query = document.getElementById('search').value; state.offset = 0; saveFilters(); loadLibrary();
}, 300));
document.querySelectorAll('.sortable').forEach(th => {
  th.addEventListener('click', () => { state.sort = th.dataset.sort; state.offset = 0; saveFilters(); loadLibrary(); });
});
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (player.style.display === 'none') return;
  switch (e.code) {
    case 'Space': e.preventDefault(); togglePlay(); break;
    case 'ArrowRight': e.preventDefault(); nextTrack(); break;
    case 'ArrowLeft': e.preventDefault(); prevTrack(); break;
    case 'ArrowUp': e.preventDefault(); adjustVolume(0.05); break;
    case 'ArrowDown': e.preventDefault(); adjustVolume(-0.05); break;
  }
});
function adjustVolume(delta) {
  const s = document.getElementById('volume-slider');
  s.value = Math.max(0, Math.min(1, parseFloat(s.value) + delta));
  audio.volume = parseFloat(s.value); s.dispatchEvent(new Event('input'));
}

// ---- Player state persistence (localStorage) ----
const STATE_KEY = 'music-loader-player';
let saveTimer = null;

function savePlayerState() {
  if (!currentTrackId) return;
  const state = {
    id: currentTrackId,
    artist: document.getElementById('player-artist').textContent,
    title: document.getElementById('player-title').textContent,
    currentTime: audio.currentTime || 0,
    volume: audio.volume,
    playing: !audio.paused,
  };
  try { localStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch (e) {}
}

// Save position periodically while playing (every 5s) and on pause/change
function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(savePlayerState, 5000);
}

audio.addEventListener('timeupdate', scheduleSave);
audio.addEventListener('pause', savePlayerState);
audio.addEventListener('play', savePlayerState);

// Volume: save immediately on change
document.getElementById('volume-slider').addEventListener('input', function() {
  audio.volume = this.value;
  const icon = this.closest('.player-volume').querySelector('.volume-icon');
  icon.textContent = this.value == 0 ? '🔇' : this.value < 0.4 ? '🔉' : '🔊';
  savePlayerState();
});

// Restore state on page load
function restorePlayerState() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null'); } catch (e) {}
  if (!saved || !saved.id) return;

  // Restore volume first
  if (saved.volume !== undefined) {
    audio.volume = saved.volume;
    document.getElementById('volume-slider').value = saved.volume;
    const icon = document.querySelector('.player-volume .volume-icon');
    if (icon) icon.textContent = saved.volume == 0 ? '🔇' : saved.volume < 0.4 ? '🔉' : '🔊';
  }

  // Restore the player UI
  currentTrackId = saved.id;
  document.getElementById('player-artist').textContent = saved.artist || '';
  document.getElementById('player-title').textContent = saved.title || '';
  player.style.display = 'block';
  mainEl.classList.add('has-player');

  // Load the stream and seek to saved position. Browsers block autoplay
  // after reload, so we always restore in paused state with the saved
  // position — the user presses play to continue.
  audio.src = `/api/library/${saved.id}/stream`;
  audio.load();
  audio.addEventListener('loadedmetadata', function onMeta() {
    audio.removeEventListener('loadedmetadata', onMeta);
    if (saved.currentTime && audio.duration && saved.currentTime < audio.duration - 1) {
      audio.currentTime = saved.currentTime;
    }
    // Update the displayed time manually (timeupdate won't fire while paused)
    document.getElementById('player-current').textContent = fmtTime(audio.currentTime || saved.currentTime || 0);
    if (audio.duration && isFinite(audio.duration)) {
      document.getElementById('player-duration').textContent = fmtTime(audio.duration);
    }
    document.getElementById('play-btn').textContent = '▶';
    updateQueueInfo();
  });

  // If the file no longer exists, hide the player gracefully
  audio.addEventListener('error', function onErr() {
    audio.removeEventListener('error', onErr);
    currentTrackId = null;
    player.style.display = 'none';
    mainEl.classList.remove('has-player');
    try { localStorage.removeItem(STATE_KEY); } catch (e) {}
  }, { once: true });
}

audio.volume = parseFloat(document.getElementById('volume-slider').value);
document.getElementById('shuffle-btn').style.opacity = '0.5';
restoreFilters();
// Reflect restored filters in the controls before the first render.
document.getElementById('search').value = state.query;
document.getElementById('sort-by').value = state.sort;
document.getElementById('sort-order').textContent = state.order === 'asc' ? '↑ Asc' : '↓ Desc';
loadStats(); loadLibrary();
restorePlayerState();

// ---- Background jobs widget ----
let jobsPollTimer = null;

async function pollJobs() {
  try {
    const r = await fetch('/api/jobs');
    if (!r.ok) return;
    const d = await r.json();
    renderJobs(d.jobs || [], d.running || 0);
  } catch (e) { /* ignore */ }
}

function renderJobs(jobs, running) {
  const toggle = document.getElementById('jobs-toggle');
  const countEl = document.getElementById('jobs-count');
  if (!running && !jobs.length) {
    toggle.style.display = 'none';
    document.getElementById('jobs-panel').style.display = 'none';
    return;
  }
  toggle.style.display = 'inline-block';
  countEl.textContent = running;

  const panel = document.getElementById('jobs-panel');
  const isOpen = panel.style.display === 'block';
  panel.innerHTML = jobs.map(j => {
    const p = j.progress || {ok: 0, failed: 0, total: 0};
    const pct = p.total > 0 ? Math.round((p.ok + p.failed) / p.total * 100) : 0;
    const statusIcon = j.done
      ? (j.error ? '❌' : '✅')
      : (j.cancelled ? '⏹' : '⏳');
    const detail = j.done && j.error
      ? `<span class="job-error">${escape(j.error)}</span>`
      : `<span class="job-meta">${p.ok} done${p.failed ? ', ' + p.failed + ' failed' : ''} / ${p.total}</span>`;
    return `<div class="job-item">
      <div class="job-head">
        <span>${statusIcon} ${escape(j.title)}</span>
        <span class="job-pct">${pct}%</span>
        ${j.done ? '' : `<button class="btn-job-stop" onclick="cancelJob('${j.id}')" title="Stop job">⏹</button>`}
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      ${detail}
    </div>`;
  }).join('');
  if (isOpen) panel.style.display = 'block';
}

async function cancelJob(id) {
  if (!confirm('Stop this job?')) return;
  try {
    await api(`/api/download/${id}`, { method: 'DELETE' });
    toast('Job stopped');
  } catch (e) { toast('Error: ' + e.message, true); }
}

function toggleJobsPanel() {
  const panel = document.getElementById('jobs-panel');
  panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  if (panel.style.display === 'block') pollJobs();
}

// ---- Sorting controls ----
function toggleSortOrder() {
  state.order = state.order === 'asc' ? 'desc' : 'asc';
  const btn = document.getElementById('sort-order');
  btn.textContent = state.order === 'asc' ? '↑ Asc' : '↓ Desc';
  state.offset = 0;
  saveFilters();
  loadLibrary();
}

document.getElementById('sort-by').addEventListener('change', () => {
  state.sort = document.getElementById('sort-by').value;
  state.offset = 0;
  saveFilters();
  loadLibrary();
});

// Poll every 2s; stop when idle to save requests
jobsPollTimer = setInterval(() => {
  const toggle = document.getElementById('jobs-toggle');
  if (toggle && toggle.style.display !== 'none') pollJobs();
}, 2000);
pollJobs();

// Close any open row menu when clicking elsewhere
document.addEventListener('click', (e) => {
  if (!e.target.closest('.dd-wrap')) closeAllMenus();
});
