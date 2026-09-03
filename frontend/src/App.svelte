<script>
  import Router from 'svelte-spa-router';
  import { getLang, setLang, t } from './lib/i18n.js';
  import Library from './routes/Library.svelte';
  import ImportView from './routes/Import.svelte';
  import Search from './routes/Search.svelte';
  import Settings from './routes/Settings.svelte';
  import Stats from './routes/Stats.svelte';
  import Login from './routes/Login.svelte';
  import Setup from './routes/Setup.svelte';
  import { api } from './lib/api.js';
  import Player from './components/Player.svelte';
  import { onMount } from 'svelte';
  // hash-based active (loc not exported in this version)

  const routes = {
    '/': Library,
    '/import': ImportView,
    '/search': Search,
    '/stats': Stats,
    '/settings': Settings,
    '/login': Login,
    '/setup': Setup,
  };

  let lang = getLang();
  function switchLang(l) { setLang(l); lang = getLang(); }

  let jobs = [];
  let running = 0;
  let jobsOpen = false;
  async function pollJobs() {
    try {
      const r = await api('/api/jobs');
      jobs = r.jobs || [];
      running = r.running || 0;
    } catch(e) {}
  }
  let _poll;
  onMount(() => {
    pollJobs();
    _poll = setInterval(() => { if (jobs.length || running) pollJobs(); }, 2000);
    return () => clearInterval(_poll);
  });
  async function cancelJob(id) {
    if (!confirm(t('common.confirmStopJob'))) return;
    try { await api('/api/download/' + id, { method: 'DELETE' }); pollJobs(); } catch(e) {}
  }
  $: active = typeof window !== 'undefined' ? (window.location.hash.replace('#','').split('?')[0] || '/') : '/';
  // keep active in sync on hash change
  if(typeof window !== 'undefined'){ window.addEventListener('hashchange', ()=> active = window.location.hash.replace('#','').split('?')[0] || '/'); }
  async function doLogout(){
    try{ await api('/api/logout', { method:'POST' }); }catch(e){}
    window.location.hash = '#/login';
  }
</script>

<nav class="topnav">
  <a href="#/" class="logo">♫ music-loader</a>
  <a href="#/import" class:active={active==='/import'}>{t('nav.import')}</a>
  <a href="#/" class:active={active==='/' }>{t('nav.library')}</a>
  <a href="#/search" class:active={active==='/search'}>{t('nav.search')}</a>
  <a href="#/stats" class:active={active==='/stats'}>{t('nav.stats')}</a>
  <a href="#/settings" class:active={active==='/settings'}>{t('nav.settings')}</a>
  <div style="margin-left:auto; display:flex; align-items:center; gap:.5rem">
    <div class="lang-switch">
      <button data-lang="en" class:active={lang==='en'} on:click={() => switchLang('en')}>EN</button>
      <button data-lang="ru" class:active={lang==='ru'} on:click={() => switchLang('ru')}>RU</button>
    </div>
    <div class="nav-jobs">
      {#if running || jobs.length}
        <button class="btn-action" on:click={() => { jobsOpen = !jobsOpen; if (jobsOpen) pollJobs(); }}>⏳ <span>{running}</span></button>
      {#if jobsOpen}
        <div class="jobs-panel" style="display:block">
          {#each jobs as j}
            {@const p = j.progress || {ok:0, failed:0, total:0}}
            {@const pct = p.total ? Math.round((p.ok+p.failed)/p.total*100) : 0}
            <div class="job-item">
              <div class="job-head"><span>{j.done ? (j.error ? '❌' : '✅') : (j.cancelled ? '⏹' : '⏳')} {j.title}</span><span class="job-pct">{pct}%</span>
                {#if !j.done}<button class="btn-job-stop" on:click={() => cancelJob(j.id)}>⏹</button>{/if}
              </div>
              <div class="progress-bar"><div class="progress-fill" style:width={pct+'%'}></div></div>
              <span class="job-meta">{p.ok} done{p.failed ? ', '+p.failed+' failed' : ''} / {p.total}</span>
            </div>
          {/each}
        </div>
        {/if}
      {/if}
    </div>
    <button class="btn-action" on:click={doLogout} title="Logout">⎋</button>
  </div>
</nav>

<Router {routes} />
<Player />
<div id="toast" style="display:none"></div>
