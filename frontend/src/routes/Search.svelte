<script>

  import { t, langStore } from '../lib/i18n.js';
  $: _lang = $langStore;
  import { api } from '../lib/api.js';
  import { playPreviewUrl } from '../lib/player.js';
  import { onMount } from 'svelte';
  let q=''; let results=[]; let sourcesSearched=0; let loading=false; let progress='';
  let searchCtl=null; let searchSeq=0; let previewCtl=null; let previewPoll=null; let dlPoll=null;
  async function doSearch(){
    if(!q.trim()) return;
    if(searchCtl) try{ searchCtl.abort(); }catch(e){}
    if(previewPoll){ clearInterval(previewPoll); previewPoll=null; }
    if(previewCtl) try{ previewCtl.abort(); }catch(e){}
    searchSeq++; const mySeq=searchSeq;
    searchCtl = new AbortController();
    loading=true; progress='';
    try{
      const d = await api('/api/search?q='+encodeURIComponent(q), { signal: searchCtl.signal });
      if(mySeq!==searchSeq) return;
      results = (d.results||[]).map(r=> ({...r, _sel:false, _previewUrl: r.preview_url || r.url})); sourcesSearched = d.sources_searched||0;
    }catch(e){ if(e.name==='AbortError') return; results=[]; sourcesSearched=0; }
    loading=false; searchCtl=null;
  }
  async function dlSelected(){
    const sel = results.filter(r=>r._sel);
    if(!sel.length) return;
    if(dlPoll){ clearInterval(dlPoll); dlPoll=null; }
    const tracks = sel.map(r=>({artist:r.artist||'', title:r.title||'', album:r.album||''}));
    const resp = await api('/api/download', { method:'POST', body: JSON.stringify({ source:'search', tracks, options:{} }) });
    progress='Started ' + resp.total + ' tracks';
    let dlTicks=0;
    dlPoll=setInterval(async()=>{
      dlTicks++;
      if(dlTicks>120){ clearInterval(dlPoll); dlPoll=null; progress='Timeout polling download'; return; }
      try{ const s=await api('/api/download/'+resp.job_id); const p=s.progress||{ok:0,failed:0,total:0}; progress = `${p.ok} ok, ${p.failed} failed / ${p.total}`; if(s.done||s.cancelled){ clearInterval(dlPoll); dlPoll=null; progress = s.cancelled ? 'Cancelled' : '✅ Complete'; } }catch(e){ clearInterval(dlPoll); dlPoll=null; }
    }, 1000);
  }
  async function preview(r){
    if(previewPoll){ clearInterval(previewPoll); previewPoll=null; }
    if(previewCtl) try{ previewCtl.abort(); }catch(e){}
    const payload = { url: r.url || r.preview_url || r.stream_url || '' };
    if(r.audio_data){ payload.audio_data = r.audio_data; payload.raw_title = r.raw_title || r.title || ''; payload.raw_artist = r.raw_artist || r.artist || ''; }
    if(!payload.url && !payload.audio_data) return;
    previewCtl = new AbortController();
    try{
      const j = await api('/api/preview', {method:'POST', body: JSON.stringify(payload), signal: previewCtl.signal});
      let ticks=0;
      previewPoll=setInterval(async()=>{
        ticks++;
        if(ticks>60){ clearInterval(previewPoll); previewPoll=null; progress='Preview timeout'; return; }
        try{
          const st = await api(`/api/preview/${j.job_id}/status`, { signal: previewCtl.signal });
          if(st.error){ clearInterval(previewPoll); previewPoll=null; progress='Preview failed: '+st.error; }
          else if(st.ready){
            clearInterval(previewPoll); previewPoll=null;
            const stream = j.stream_url || `/api/preview/${j.job_id}/stream`;
            playPreviewUrl(stream, { artist: r.artist||'', title: r.title||'' });
          }
        }catch(e){ if(e.name==='AbortError') { clearInterval(previewPoll); previewPoll=null; } }
      }, 500);
    }catch(e){ if(e.name==='AbortError') return; }
  }
  onMount(()=>{ return ()=>{ if(searchCtl) try{searchCtl.abort();}catch(e){}; if(previewCtl) try{previewCtl.abort();}catch(e){}; if(previewPoll) clearInterval(previewPoll); if(dlPoll) clearInterval(dlPoll); }; });

</script>


<main>
  <header class="page-header"><h1>{t('search.title')}</h1></header>
  <div class="search-box"><input type="search" placeholder={t('search.placeholder')} bind:value={q} on:keydown={(e)=>{ if(e.key==='Enter') doSearch(); }}><button class="btn-primary" style="margin-top:0" on:click={doSearch}>{t('search.btn')}</button></div>
  <p class="hint"><span>{t('search.hint')}</span> <code>{t('search.hint.example')}</code> {t('search.orJust')} <code>Never Gonna Give You Up</code></p>
  {#if progress}<p class="hint">{progress}</p>{/if}
  {#if loading}<p class="hint">{t('common.searching')}</p>{/if}
  {#if results.length}
    <div class="toolbar"><span class="count">{t('search.results', {n: results.length, s: sourcesSearched})}</span><button class="btn-primary" style="margin-top:0" on:click={dlSelected}>{t('search.downloadSelected')}</button></div>
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
  {:else if !loading && q}
    <p class="hint">{t('search.noResults')}</p>
  {/if}
</main>

