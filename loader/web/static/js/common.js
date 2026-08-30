/* Shared helpers: toast, escape, debounce, jobs widget. Loaded via layout.html before page scripts. */
function escape(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function toast(msg, err) {
  var el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg; el.className = err ? 'toast-error' : 'toast-info';
  el.style.display = 'block';
  setTimeout(function() { el.style.display = 'none'; }, 3000);
}
function debounce(fn, ms) { var t; return function() { var a = arguments; clearTimeout(t); t = setTimeout(function() { fn.apply(null, a); }, ms); }; }

// ---- Background jobs widget (shared across all pages) ----
async function pollJobs() {
  try {
    var r = await fetch('/api/jobs');
    if (!r.ok) return;
    var d = await r.json();
    renderJobs(d.jobs || [], d.running || 0);
  } catch (e) {}
}
function renderJobs(jobs, running) {
  var toggle = document.getElementById('jobs-toggle');
  if (!toggle) return;
  if (!running && !jobs.length) {
    toggle.style.display = 'none';
    var p = document.getElementById('jobs-panel');
    if (p) p.style.display = 'none';
    return;
  }
  toggle.style.display = 'inline-block';
  var countEl = document.getElementById('jobs-count');
  if (countEl) countEl.textContent = running;
  var panel = document.getElementById('jobs-panel');
  if (!panel) return;
  var isOpen = panel.style.display === 'block';
  panel.innerHTML = jobs.map(function(j) {
    var p = j.progress || {ok: 0, failed: 0, total: 0};
    var pct = p.total > 0 ? Math.round((p.ok + p.failed) / p.total * 100) : 0;
    var statusIcon = j.done ? (j.error ? '❌' : '✅') : (j.cancelled ? '⏹' : '⏳');
    var detail = j.done && j.error
      ? '<span class="job-error">' + escape(j.error) + '</span>'
      : '<span class="job-meta">' + p.ok + ' done' + (p.failed ? ', ' + p.failed + ' failed' : '') + ' / ' + p.total + '</span>';
    var stop = j.done ? '' : '<button class="btn-job-stop" onclick="cancelJob(\'' + j.id + '\')" title="Stop job">⏹</button>';
    return '<div class="job-item"><div class="job-head"><span>' + statusIcon + ' ' + escape(j.title) + '</span><span class="job-pct">' + pct + '%</span>' + stop + '</div><div class="progress-bar"><div class="progress-fill" style="width:' + pct + '%"></div></div>' + detail + '</div>';
  }).join('');
  if (isOpen) panel.style.display = 'block';
}
async function cancelJob(id) {
  if (!confirm((typeof t === 'function' ? t('common.confirmStopJob') : 'Stop this job?'))) return;
  try {
    var r = await fetch('/api/download/' + id, { method: 'DELETE', headers: {'Content-Type': 'application/json'} });
    if (!r.ok) { var e = await r.json().catch(function(){ return {error: r.statusText}; }); throw new Error(e.error || r.statusText); }
    toast(typeof t === 'function' ? t('common.jobStopped') : 'Job stopped');
  } catch (e) { toast((typeof t === 'function' ? t('common.error', {msg: e.message}) : 'Error: ' + e.message), true); }
}
function toggleJobsPanel() {
  var panel = document.getElementById('jobs-panel');
  if (!panel) return;
  panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  if (panel.style.display === 'block') pollJobs();
}
(function() {
  setInterval(function() {
    var toggle = document.getElementById('jobs-toggle');
    if (toggle && toggle.style.display !== 'none') pollJobs();
  }, 2000);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pollJobs);
  else pollJobs();
})();
