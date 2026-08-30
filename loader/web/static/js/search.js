
let lastResults = [];
let searchTimeout = null;

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) { const e = await r.json().catch(() => ({error: r.statusText})); throw new Error(e.error || r.statusText); }
  return r.json();
}

function escape(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function fmtSize(b) {
  if (!b) return '—';
  if (b > 1e9) return (b/1e9).toFixed(1)+'GB';
  if (b > 1e6) return (b/1e6).toFixed(1)+'MB';
  return (b/1e3).toFixed(0)+'KB';
}
function toast(msg, err = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = err ? 'toast-error' : 'toast-info';
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// ---- Search ----
document.getElementById('search-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doSearch();
});

async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) { toast(t('common.searching'), true); return; }

  const resultsDiv = document.getElementById('results');
  resultsDiv.style.display = 'block';
  document.getElementById('results-list').innerHTML = '<div class="loading"><div class="spinner"></div><span class="hint">' + t('common.loading') + '</span></div>';

  try {
    const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
    lastResults = data.results || [];
    renderResults(data);
  } catch (e) {
    document.getElementById('results-list').innerHTML = `<p class="error">Error: ${escape(e.message)}</p>`;
  }
}

function renderResults(data) {
  const results = data.results || [];
  const el = document.getElementById('results-list');
  document.getElementById('result-count').textContent = t('search.results', {n: results.length, s: data.sources_searched || '?'});

  if (!results.length) {
    el.innerHTML = '<p class="empty">' + t('search.noResults') + '</p>';
    return;
  }

  el.innerHTML = results.map(function(r, i) {
    var score = r.match_score ? (r.match_score * 100).toFixed(0) + '%' : '—';
    var playAttr = r.url ? ' data-url="' + r.url.replace(/"/g, '&quot;') + '"' : '';
    var durCell = r.duration ? '<span class="result-dur">' + fmtDur(r.duration) + '</span>' : '';
    return '<div class="result-card">'
      + '<label class="result-label">'
      + '<input type="checkbox" class="result-cb" data-index="' + i + '">'
      + '<button class="btn-play" data-preview="1"' + playAttr + '>▶</button>'
      + '<div class="result-info">'
      + '<span class="result-artist">' + escape(r.artist || '?') + '</span>'
      + '<span class="result-title">' + escape(r.title || '?') + '</span>'
      + '<span class="result-album">' + escape(r.album || '') + '</span>'
      + '</div>'
      + '<div class="result-meta">'
      + '<span class="badge badge-ok">' + escape(r.source || '?') + '</span>'
      + '<span class="result-score">' + score + '</span>'
      + '</div>'
      + '</label>'
      + '</div>';
  }).join('');

  // Delegate preview clicks
  el.querySelectorAll('[data-preview]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      previewTrack(this.dataset.url);
    });
  });
}

// ---- Preview player (same UI as library) ----
var previewAudio = document.getElementById('preview-audio');
var previewPlayer = document.getElementById('player');
var mainEl = document.querySelector('main');

previewAudio.addEventListener('timeupdate', function() {
  if (previewAudio.duration) {
    document.getElementById('player-progress-fill').style.width = (previewAudio.currentTime / previewAudio.duration * 100) + '%';
    document.getElementById('player-current').textContent = fmtTime(previewAudio.currentTime);
  }
});
previewAudio.addEventListener('loadedmetadata', function() {
  if (previewAudio.duration && isFinite(previewAudio.duration))
    document.getElementById('player-duration').textContent = fmtTime(previewAudio.duration);
});
previewAudio.addEventListener('ended', function() {
  document.getElementById('player-progress-fill').style.width = '0%';
  var b = document.getElementById('preview-play-btn');
  if (b) b.textContent = '▶';
});

function togglePreviewPlay() {
  if (previewAudio.paused) {
    previewAudio.play();
    document.getElementById('preview-play-btn').textContent = '⏸';
  } else {
    previewAudio.pause();
    document.getElementById('preview-play-btn').textContent = '▶';
  }
}

function previewTrack(url) {
  if (!url) return;

  // Stop any currently playing preview
  previewAudio.pause();
  previewAudio.src = '';

  previewPlayer.style.display = 'block';
  if (mainEl) mainEl.classList.add('has-player');
  document.getElementById('player-artist').textContent = t('search.preview.loading');
  document.getElementById('player-title').textContent = t('search.preview.downloading');
  document.getElementById('player-progress-fill').style.width = '10%';
  document.getElementById('player-current').textContent = '0:00';
  document.getElementById('player-duration').textContent = '0:00';
  document.getElementById('preview-play-btn').textContent = '⏸';

  // Step 1: POST to start download
  fetch('/api/preview', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url: url}),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.error) { throw new Error(data.error); }
    var jobId = data.job_id;
    // Step 2: poll until ready
    var iv = setInterval(function() {
      fetch('/api/preview/' + jobId + '/status')
        .then(function(r) { return r.json(); })
        .then(function(s) {
          if (s.error) {
            clearInterval(iv);
            document.getElementById('player-title').textContent = t('search.preview.failed', {msg: s.error});
            document.getElementById('player-progress-fill').style.width = '0%';
            return;
          }
          if (s.ready) {
            clearInterval(iv);
            document.getElementById('player-artist').textContent = 'Preview';
            document.getElementById('player-title').textContent = t('search.preview.streaming');
            document.getElementById('player-progress-fill').style.width = '0%';
            previewAudio.src = '/api/preview/' + jobId + '/stream';
            previewAudio.load();
            previewAudio.play().catch(function() {});
          }
        })
        .catch(function(e) {
          clearInterval(iv);
          document.getElementById('player-title').textContent = 'Status error';
        });
    }, 500);
  })
  .catch(function(e) {
    document.getElementById('player-title').textContent = t('search.preview.failed', {msg: e.message});
    document.getElementById('player-progress-fill').style.width = '0%';
  });
}

function closePreview() {
  previewAudio.pause();
  previewAudio.src = '';
  previewPlayer.style.display = 'none';
  if (mainEl) mainEl.classList.remove('has-player');
  document.getElementById('player-progress-fill').style.width = '0%';
}

function seek(e) {
  var rect = e.currentTarget.getBoundingClientRect();
  var pct = (e.clientX - rect.left) / rect.width;
  if (previewAudio.duration) previewAudio.currentTime = pct * previewAudio.duration;
}

function fmtTime(sec) {
  if (!sec || !isFinite(sec)) return '0:00';
  var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m + ':' + (s < 10 ? '0' : '') + s;
}

// Volume
if (document.getElementById('volume-slider')) {
  document.getElementById('volume-slider').addEventListener('input', function() {
    previewAudio.volume = this.value;
    var icon = this.closest('.player-volume').querySelector('.volume-icon');
    icon.textContent = this.value == 0 ? '🔇' : this.value < 0.4 ? '🔉' : '🔊';
  });
  previewAudio.volume = parseFloat(document.getElementById('volume-slider').value);
}

function fmtDur(sec) {
  if (!sec || !isFinite(sec)) return '—';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m + ':' + (s < 10 ? '0' : '') + s;
}

function getSelectedResults() {
  const indices = [];
  document.querySelectorAll('.result-cb:checked').forEach(cb => {
    indices.push(parseInt(cb.dataset.index));
  });
  return indices.map(i => lastResults[i]);
}

async function downloadSelected() {
  const selected = getSelectedResults();
  if (!selected.length) { toast(t('search.noResults'), true); return; }

  // Send structured tracks — no text round-trip, so titles/albums
  // containing " - " survive intact.
  const tracks = selected.map(r => ({
    artist: r.artist || '',
    title: r.title || '',
    album: r.album || '',
  }));

  const prog = document.getElementById('progress');
  prog.style.display = 'block';
  document.getElementById('progress-fill').style.width = '0%';
  document.getElementById('progress-text').textContent = t('common.loading');

  try {
    const resp = await api('/api/download', {
      method: 'POST',
      body: JSON.stringify({
        source: 'search',
        tracks,
        options: {
          name: 'Search results',
          quality: document.getElementById('dl-quality')?.value || '128',
          parallel: 4,
          enrich: true,
        },
      }),
    });
    pollJob(resp.job_id, resp.total);
  } catch (e) {
    toast(t('common.error', {msg: e.message}), true);
    prog.style.display = 'none';
  }
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
        document.getElementById('progress-text').textContent = s.cancelled ? t('common.cancelled') : t('common.complete');
      }
    } catch (e) { clearInterval(interval); }
  }, 1000);
}

window.addEventListener('langchange', function() { applyI18n(); });

