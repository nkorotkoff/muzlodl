
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
      ? ' <span class="badge badge-interrupted">' + t('history.interrupted') + '</span>' : '';
    return `<tr>
      <td>${escape(dateStr)}</td>
      <td>${escape(s.source_name || s.source)}${flag}</td>
      <td>${s.total}</td>
      <td>${s.downloaded}</td>
      <td>${s.failed}</td>
      <td><button onclick="showDetail(${s.id},'${escape(s.source_name || s.source)}')">${t('history.view')}</button></td>
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
    t('history.importTracks', {name: name, n: data.total});
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
window.addEventListener('langchange', function() { applyI18n(); load(); });


