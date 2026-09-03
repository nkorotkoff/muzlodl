<script>

  import { t } from '../lib/i18n.js';
  import { api } from '../lib/api.js';
  import { onMount } from 'svelte';
  let q=''; let results=[]; let loading=false; let progress='';
  async function doSearch(){
    if(!q.trim()) return;
    loading=true; progress='';
    try{ const d = await api('/api/search?q='+encodeURIComponent(q)); results = (d.results||[]).map(r=> ({...r, _sel:false, _previewUrl: r.preview_url || r.url})); }catch(e){ results=[]; } loading=false;
  }
  async function dlSelected(){
    const sel = results.filter(r=>r._sel);
    if(!sel.length) return;
    const tracks = sel.map(r=>({artist:r.artist||'', title:r.title||'', album:r.album||''}));
    const resp = await api('/api/download', { method:'POST', body: JSON.stringify({ source:'search', tracks, options:{} }) });
    progress='Started ' + resp.total + ' tracks';
    const iv=setInterval(async()=>{
      try{ const s=await api('/api/download/'+resp.job_id); const p=s.progress||{ok:0,failed:0,total:0}; progress = `${p.ok} ok, ${p.failed} failed / ${p.total}`; if(s.done||s.cancelled){ clearInterval(iv); progress = s.cancelled ? 'Cancelled' : '✅ Complete'; } }catch(e){ clearInterval(iv); }
    }, 1000);
  }
  let previewUrl=null; let previewAudio;
  async function preview(r){
    const url = r.url || r.preview_url || r.stream_url;
    if(!url) return;
    try{
      const j = await api('/api/preview', {method:'POST', body: JSON.stringify({url})});
      // poll status
      const poll = setInterval(async()=>{
        try{
          const st = await api(`/api/preview/${j.job_id}/status`);
          if(st.error){ clearInterval(poll); progress='Preview failed: '+st.error; }
          if(st.ready){ clearInterval(poll); previewUrl = j.stream_url || `/api/preview/${j.job_id}/stream`; if(previewAudio){ previewAudio.src = previewUrl; previewAudio.play(); } }
        }catch(e){}
      }, 500);
    }catch(e){}
  }

</script>


<main>
  <header class="page-header"><h1>{t('search.title')}</h1></header>
  <div class="search-box"><input type="search" placeholder={t('search.placeholder')} bind:value={q} on:keydown={(e)=>{ if(e.key==='Enter') doSearch(); }}><button class="btn-primary" style="margin-top:0" on:click={doSearch}>{t('search.btn')}</button></div>
  <p class="hint"><span>{t('search.hint')}</span> <code>{t('search.hint.example')}</code> {t('search.orJust')} <code>Never Gonna Give You Up</code></p>
  {#if progress}<p class="hint">{progress}</p>{/if}
  {#if loading}<p class="hint">{t('common.searching')}</p>{/if}
  {#if results.length}
    <div class="toolbar"><span class="count">{results.length} {t('search.results', {n: results.length, s: 'sources'})}</span><button class="btn-primary" style="margin-top:0" on:click={dlSelected}>{t('search.downloadSelected')}</button></div>
    {#each results as r}
      <label class="result-card">
        <span class="result-label">
          <input type="checkbox" bind:checked={r._sel}>
          <span class="result-info"><span class="result-artist">{r.artist}</span><span class="result-title">{r.title}</span><span class="result-album">{r.album}</span></span>
          <span class="result-meta">{r.source} {r.score ? Math.round(r.score*100)+'%' : ''} {r.duration ? Math.round(r.duration)+'s' : ''}</span>
          <button class="btn-action" on:click={()=>preview(r)}>▶</button>
        </span>
      </label>
    {/each}
    <audio bind:this={previewAudio} controls style="width:100%;margin-top:1rem" preload="none"></audio>
  {:else if !loading && q}
    <p class="hint">{t('search.noResults')}</p>
  {/if}
</main>

