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

// ---- Tabs ----
function switchTab(name) {
  document.querySelectorAll('.settings-tabs [role="tab"]').forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle('tab-active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('[data-tab-panel]').forEach(p => { p.hidden = p.dataset.tabPanel !== name; });
  try { localStorage.setItem('settings-tab', name); } catch {}
}
function initTabs() {
  const saved = (() => { try { return localStorage.getItem('settings-tab'); } catch { return null; } })();
  const first = document.querySelector('.settings-tabs [role="tab"]');
  switchTab(saved && document.querySelector(`[data-tab="${saved}"]`) ? saved : (first ? first.dataset.tab : 'general'));
  document.querySelectorAll('.settings-tabs [role="tab"]').forEach(b => b.addEventListener('click', () => switchTab(b.dataset.tab)));
}

// ---- Settings (auto-save) ----
let _saveTimer = null;
let _saving = false;
function setIndicator(key) {
  const el = document.getElementById('save-indicator');
  if (!el) return;
  el.textContent = key ? t(key) : '';
}
function scheduleSave() {
  if (_saveTimer) clearTimeout(_saveTimer);
  setIndicator('settings.saving');
  _saveTimer = setTimeout(() => doSave().catch(e => { setIndicator(''); toast(t('common.error', {msg: e.message}), true); }), 400);
}
async function doSave() {
  if (_saving) return;
  _saving = true;
  const body = {
    admin_username: document.getElementById('s-username').value.trim(),
    password: document.getElementById('s-password').value,
    acoustid_api_key: document.getElementById('s-acoustid-key').value,
    acoustid_verify: document.getElementById('s-acoustid-verify').checked ? 'true' : 'false',
    acoustid_min_score: document.getElementById('s-acoustid-score').value,
    sources_auto: document.getElementById('s-sources-auto').checked ? 'true' : 'false',
    sources: JSON.stringify(window._sources || []),
  };
  await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) });
  setIndicator('settings.saved');
  setTimeout(() => setIndicator(''), 1500);
  _saving = false;
  updateSourcesAutoUI();
}

function isAuto() {
  const el = document.getElementById('s-sources-auto');
  return !el || el.checked;
}
function updateSourcesAutoUI() {
  const auto = isAuto();
  const status = document.getElementById('sources-auto-status');
  const adv = document.getElementById('sources-advanced');
  if (status) {
    if (auto) {
      status.style.display = 'block';
      status.textContent = t('settings.autoActive') + ': ' + (window._sources || []).join(', ');
    } else {
      status.style.display = 'none';
    }
  }
  if (adv) adv.open = !auto;
  renderSources();
}

async function loadSettings() {
  const s = await api('/api/settings');
  document.getElementById('s-username').value = s.admin_username || 'admin';
  document.getElementById('s-password').value = '';
  document.getElementById('s-acoustid-key').value = s.acoustid_api_key || '';
  document.getElementById('s-acoustid-verify').checked = s.acoustid_verify === 'true';
  document.getElementById('s-acoustid-score').value = s.acoustid_min_score || '0.5';

  const sources = JSON.parse(s.sources || '[]');
  window._sources = sources;
  const auto = (s.sources_auto || 'true') === 'true';
  document.getElementById('s-sources-auto').checked = auto;
  updateSourcesAutoUI();
}

function renderSources() {
  const list = document.getElementById('sources-list');
  const auto = isAuto();
  list.innerHTML = window._sources.map((src, i) => `
    <div class="source-row">
      <span class="source-name">${escape(src)}</span>
      <button onclick="moveSource(${i}, -1)" ${(auto || i === 0) ? 'disabled' : ''}>↑</button>
      <button onclick="moveSource(${i}, 1)" ${(auto || i === window._sources.length - 1) ? 'disabled' : ''}>↓</button>
    </div>
  `).join('');
}

function moveSource(idx, dir) {
  if (isAuto()) return;
  const arr = window._sources;
  const other = idx + dir;
  if (other < 0 || other >= arr.length) return;
  [arr[idx], arr[other]] = [arr[other], arr[idx]];
  window._sources = arr;
  renderSources();
  scheduleSave();
}

// saveSettings kept as alias for inline handlers/tests
async function saveSettings() { return doSave(); }

// ---- Doctor ----
async function runDoctor() {
  const el = document.getElementById('doctor-results');
  el.style.display = 'block';
  el.innerHTML = '<p>' + t('settings.testing') + '</p>';
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
      el.innerHTML = t('settings.cloudNotConfigured');
      document.getElementById('cloud-show-setup').style.display = 'inline-block';
    } else {
      el.innerHTML = `Backend: <strong>${escape(s.backend)}</strong> · Reachable: <strong>${s.reachable ? '✅' : '❌'}</strong>`;
      document.getElementById('cloud-show-setup').style.display = 'inline-block';
    }
  } catch (e) {
    document.getElementById('cloud-status').textContent = t('common.error', {msg: e.message});
  }
}

async function saveCloudConfig() {
  const backend = document.getElementById('cloud-backend').value;
  const login = document.getElementById('cloud-login').value;
  const password = document.getElementById('cloud-password').value;
  const root = document.getElementById('cloud-root').value;

  if (backend === 'yandex_rest') {
    if (!password) { toast(t('common.pasteToken'), true); return; }
  } else if (!login || !password) {
    toast(t('common.fillLogin'), true); return;
  }

  try {
    await api('/api/cloud/config', {
      method: 'POST',
      body: JSON.stringify({ backend, login, password, root }),
    });
    toast(t('common.configured'));
    checkCloud();
  } catch (e) {
    toast(t('common.error', {msg: e.message}), true);
  }
}

// Toggle fields based on backend type
document.getElementById('cloud-backend').addEventListener('change', function() {
  const isRest = this.value === 'yandex_rest';
  document.getElementById('cloud-login-wrap').style.display = isRest ? 'none' : 'block';
  document.getElementById('cloud-password-label').querySelector('span').textContent = isRest ? t('settings.oauthToken') : t('settings.appPassword');
  document.getElementById('cloud-rest-hint').style.display = isRest ? 'block' : 'none';
});

async function clearCloudConfig() {
  await api('/api/cloud/config', { method: 'DELETE' });
  toast(t('common.cleared'));
  checkCloud();
}

async function uploadCloud() {
  const el = document.getElementById('cloud-upload-status');
  el.textContent = t('settings.uploading');
  try {
    const r = await api('/api/cloud/upload', { method: 'POST' });
    if (r.job_id) {
      const iv = setInterval(async () => {
        const s = await api(`/api/download/${r.job_id}`);
        const p = s.progress;
        if (p.total > 1) el.textContent = t('settings.uploading') + ' ' + p.ok + ' / ' + p.total;
        if (s.done) {
          clearInterval(iv);
          el.textContent = s.error ? t('common.error', {msg: s.error}) : t('settings.reencodeDone');
        }
      }, 1000);
    }
  } catch (e) {
    el.textContent = t('common.error', {msg: e.message});
  }
}

async function startReencode() {
  const bitrate = parseInt(document.getElementById('reencode-bitrate').value);
  if (!confirm(t('settings.reencodeConfirm', {n: bitrate}))) return;
  const box = document.getElementById('reencode-progress');
  box.style.display = 'block';
  document.getElementById('reencode-fill').style.width = '0%';
  document.getElementById('reencode-text').textContent = t('settings.starting');
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
        document.getElementById('reencode-text').textContent = t('settings.reencodeDone');
        toast(t('settings.reencodeDone'));
      }
    }, 1000);
  } catch (e) {
    document.getElementById('reencode-text').textContent = t('common.error', {msg: e.message});
  }
}

// ---- Maintenance ----
async function retryFailed() {
  const el = document.getElementById('maint-result');
  el.textContent = t('settings.retrying');
  try {
    const r = await api('/api/library/retry-failed', { method: 'POST' });
    if (r.total === 0) {
      el.textContent = t('settings.noFailed');
    } else {
      el.textContent = t('settings.retryingCount', {n: r.total});
    }
  } catch (e) { el.textContent = t('common.error', {msg: e.message}); }
}
Object.assign(window, { saveSettings, runDoctor, checkCloud, saveCloudConfig, clearCloudConfig, uploadCloud, startReencode, retryFailed, moveSource, switchTab });

document.getElementById('s-sources-auto').addEventListener('change', () => { updateSourcesAutoUI(); scheduleSave(); });
document.getElementById('s-acoustid-key').addEventListener('input', scheduleSave);
document.getElementById('s-acoustid-verify').addEventListener('change', scheduleSave);
document.getElementById('s-acoustid-score').addEventListener('change', scheduleSave);
document.getElementById('s-lang').addEventListener('change', scheduleSave);
document.getElementById('s-username').addEventListener('change', scheduleSave);
let _pwdTimer = null;
document.getElementById('s-password').addEventListener('input', () => {
  if (_pwdTimer) clearTimeout(_pwdTimer);
  _pwdTimer = setTimeout(() => { if (document.getElementById('s-password').value) scheduleSave(); }, 900);
});

initTabs();
loadSettings();
checkCloud();
window.addEventListener('langchange', function() {
  applyI18n();
  document.getElementById('cloud-password-label').querySelector('span').textContent =
    document.getElementById('cloud-backend').value === 'yandex_rest' ? t('settings.oauthToken') : t('settings.appPassword');
  updateSourcesAutoUI();
  if (document.getElementById('save-indicator').textContent) setIndicator('settings.saved');
});
