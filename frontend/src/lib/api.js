export function getCookie(name) {
  const m = document.cookie.match('(?:^|;\\s*)' + name + '=([^;]*)');
  return m ? decodeURIComponent(m[1]) : '';
}
export async function api(path, opts = {}) {
  const headers = { 'Accept': 'application/json', 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const method = (opts.method || 'GET').toUpperCase();
  if (!['GET','HEAD','OPTIONS'].includes(method)) {
    const token = getCookie('csrf_token');
    if (token) headers['X-CSRF-Token'] = token;
  }
  const r = await fetch(path, { ...opts, headers });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }));
    throw new Error(e.error || r.statusText);
  }
  const ct = r.headers.get('content-type') || '';
  if (ct.includes('application/json')) return r.json();
  return r;
}
export function toast(msg, err=false) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = err ? 'toast-error' : 'toast-info';
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}
export function escapeHtml(s) {
  const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML;
}
export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
