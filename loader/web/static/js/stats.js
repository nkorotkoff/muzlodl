// Stats page
function escape(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
}
function tr(k, p) { try { return t(k, p); } catch(e) { return k; } }

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();

    const s = d.summary || {};
    document.getElementById('summary-stats').textContent =
      tr('stats.summary', {total: s.total_plays || 0, unique: s.unique_tracks || 0, week: s.last_7_days || 0});

    // Top tracks
    const tt = document.getElementById('top-tracks');
    tt.innerHTML = (d.top_tracks || []).length
      ? d.top_tracks.map((t, i) => `
        <div class="stat-row">
          <span class="stat-rank">${i + 1}</span>
          <div class="stat-info">
            <span class="stat-title">${escape(t.title)}</span>
            <span class="stat-sub">${escape(t.artist)}${t.album ? ' · ' + escape(t.album) : ''}</span>
          </div>
          <span class="stat-count">${t.plays}×</span>
        </div>`).join('')
      : '<p class="hint">' + tr('stats.noPlays') + '</p>';

    // Top artists
    const ta = document.getElementById('top-artists');
    ta.innerHTML = (d.top_artists || []).length
      ? d.top_artists.map((a, i) => `
        <div class="stat-row">
          <span class="stat-rank">${i + 1}</span>
          <div class="stat-info">
            <span class="stat-title">${escape(a.artist)}</span>
            <span class="stat-sub">${a.tracks} tracks</span>
          </div>
          <span class="stat-count">${a.plays}×</span>
        </div>`).join('')
      : '<p class="hint">' + tr('stats.noPlaysShort') + '</p>';

    // Recently played
    const rl = document.getElementById('recent-list');
    rl.innerHTML = (d.recent || []).length
      ? d.recent.map(t => `
        <div class="stat-row">
          <div class="stat-info">
            <span class="stat-title">${escape(t.artist)} — ${escape(t.title)}</span>
            <span class="stat-sub">${escape(t.album || '')}</span>
          </div>
          <span class="stat-count stat-time">${fmtTime(t.played_at)}</span>
        </div>`).join('')
      : '<p class="hint">' + tr('stats.noPlaysShort') + '</p>';
  } catch (e) {
    document.getElementById('top-tracks').innerHTML = '<p class="error">Failed to load stats</p>';
  }
}

loadStats();
window.addEventListener('langchange', function() { try { applyI18n(); loadStats(); } catch(e) {} });
