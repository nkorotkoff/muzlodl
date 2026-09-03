<script>

  import { t } from '../lib/i18n.js';
  import { api, escapeHtml, debounce } from '../lib/api.js';
  import { onMount } from 'svelte';
  import { currentId as _pc, isPlaying as _isPlaying } from '../lib/player.js';
  let isPlayingVal=false; _isPlaying.subscribe(v=> isPlayingVal=v);

  // ---- State ----
  let tracks = []; let total=0; let offset=0; let limit=100; let query=''; let sort='artist'; let order='asc'; let loading=false; let totalStats='';
  let selected = new Set();
  let editId=null; let editArtist='', editTitle='', editAlbum='';
  let uploadFile=null; let uploadArtist='', uploadTitle='', uploadAlbum='';
  let showEdit=false; let showUpload=false;
  let currentId=null;
  _pc.subscribe(v=> currentId=v);
  let sentinelEl;
  let countEl = 0;
  const FILTERS_KEY='library-filters';
  function saveFilters(){ try{ localStorage.setItem(FILTERS_KEY, JSON.stringify({query, sort, order})); }catch(e){} }
  function restoreFilters(){ try{ const f=JSON.parse(localStorage.getItem(FILTERS_KEY)||'null'); if(!f) return; if(typeof f.query==='string') query=f.query; if(typeof f.sort==='string') sort=f.sort; if(typeof f.order==='string') order=f.order; }catch(e){} }
  restoreFilters();
  async function fetchTracks(params){ const q=new URLSearchParams(params); return api('/api/library?'+q.toString()); }
  async function load(reset=false){
    if(reset){ tracks=[]; offset=0; selected=new Set(); selected=selected; }
    loading=true;
    try{
      const d = await fetchTracks({ q: query, sort, order, limit: limit, offset: offset });
      if(reset) tracks = d.tracks; else tracks = [...tracks, ...d.tracks];
      total = d.total; offset += d.tracks.length; countEl = total;
      // stats
      try{ const s = await api('/api/disk'); totalStats = s.size_human ? `${s.artists} artists · ${(s.files ?? s.count)} files · ${s.size_human}` : ''; }catch(e){}
    }catch(e){} loading=false;
  }
  function handleSort(col){ if(sort===col) order = order==='asc'?'desc':'asc'; else { sort=col; order='asc'; } saveFilters(); load(true); }
  let _qTimer=null;
  function onSearchInput(e){ query = e.target.value; saveFilters(); clearTimeout(_qTimer); _qTimer=setTimeout(()=> load(true), 300); }
  function toggleSelect(id){ const n=new Set(selected); if(n.has(id)) n.delete(id); else n.add(id); selected=n; }
  function selectAll(e){ const c=e.target.checked; if(c) selected=new Set(tracks.map(t=>t.id)); else selected=new Set(); selected=selected; }
  async function batchDelete(){
    if(!selected.size) return;
    if(!confirm(t('common.confirmDeleteCount', {n: selected.size}))) return;
    await api('/api/library/batch-delete', {method:'POST', body: JSON.stringify({ids: [...selected]})});
    selected=new Set(); selected=selected; load(true);
  }
  function clearSel(){ selected=new Set(); selected=selected; }
  function openEdit(tr){ editId=tr.id; editArtist=tr.artist; editTitle=tr.title; editAlbum=tr.album; showEdit=true; }
  async function saveEdit(){
    if(!editId) return;
    await api(`/api/library/${editId}`, {method:'PATCH', body: JSON.stringify({artist: editArtist, title: editTitle, album: editAlbum})});
    showEdit=false; load(true);
  }
  function showUploadModal(){ showUpload=true; }
  async function doUpload(){
    if(!uploadFile) return;
    const fd=new FormData(); fd.append('file', uploadFile);
    if(uploadArtist) fd.append('artist', uploadArtist);
    if(uploadTitle) fd.append('title', uploadTitle);
    if(uploadAlbum) fd.append('album', uploadAlbum);
    // CSRF header manually for FormData
    const m=document.cookie.match('(?:^|;\\s*)csrf_token=([^;]*)');
    const token=m?decodeURIComponent(m[1]):'';
    const headers= token ? {'X-CSRF-Token': token} : {};
    const r=await fetch('/api/library/upload', {method:'POST', headers, body: fd});
    if(!r.ok){ const e=await r.json().catch(()=>({error:r.statusText})); alert(e.error); return; }
    showUpload=false; uploadFile=null; load(true);
  }
  async function delTrack(id){
    if(!confirm(t('common.confirmDelete'))) return;
    await api(`/api/library/${id}`, {method:'DELETE'});
    load(true);
  }
  async function retryTrack(id){ await api(`/api/library/${id}/retry`, {method:'POST'}); }
  async function reloadTrack(id){ await api(`/api/library/${id}/reload`, {method:'POST'}); }
  function fmtDur(s){ if(!s) return '—'; const m=Math.floor(s/60), sec=Math.round(s%60); return m+':'+String(sec).padStart(2,'0'); }
  function fmtSize(b){ if(!b) return '—'; if(b>1e9) return (b/1e9).toFixed(1)+'GB'; if(b>1e6) return (b/1e6).toFixed(1)+'MB'; return (b/1e3).toFixed(0)+'KB'; }
  function fmtTime(s){ if(!s) return ''; return new Date(s).toLocaleDateString(); }
  // Player delegates to global store (queue in lib/player.js)
  async function playTrack(tr){ const { playTrack: gp } = await import('../lib/player.js'); await gp(tr, async (p)=> fetchTracks({ q: query, sort, order, ...p })); }
  function nextTrack(){ import('../lib/player.js').then(m=> m.next()); }
  function prevTrack(){ import('../lib/player.js').then(m=> m.prev()); }
  function togglePlay(){ import('../lib/player.js').then(m=> m.togglePlay()); }
  function onAudioEnded(){ import('../lib/player.js').then(m=> m.next()); }

  let _sentinelObs=null;
  onMount(()=>{
    load(true);
    const obs = new IntersectionObserver((entries)=>{
      for(const e of entries){ if(e.isIntersecting && !loading && tracks.length < total){ load(false); } }
    }, {rootMargin:'300px'});
    _sentinelObs = obs;
    if(sentinelEl) obs.observe(sentinelEl);
    return ()=> { obs.disconnect(); _sentinelObs=null; };
  });
  $: if(sentinelEl && _sentinelObs){ try{ _sentinelObs.observe(sentinelEl); }catch(e){} }




</script>


<main>
  <header class="page-header"><h1>{t('library.title')}</h1><span class="stats">{totalStats}</span></header>
  <div class="toolbar">
    <input type="search" placeholder={t('library.searchPlaceholder')} value={query} on:input={onSearchInput} autofocus>
    <button on:click={showUploadModal} class="btn-action">{t('library.btn.upload')}</button>
    <button on:click={async ()=>{ const fmt='csv'; const r=await fetch(`/api/library/export?format=${fmt}`); const blob=await r.blob(); const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=`library.${fmt}`; a.click(); URL.revokeObjectURL(url); }} class="btn-action">⬇ CSV</button>
    <button on:click={async ()=>{ const fmt='json'; const r=await fetch(`/api/library/export?format=${fmt}`); const blob=await r.blob(); const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=`library.${fmt}`; a.click(); URL.revokeObjectURL(url); }} class="btn-action">⬇ JSON</button>
    <span class="count">{tracks.length} / {total}</span>
  </div>
  {#if selected.size}
    <div class="batch-bar"><span>{t('library.batch.selected', {n: selected.size})}</span>
      <button on:click={batchDelete} class="btn-danger">{t('library.batch.delete')}</button>
      <button on:click={clearSel}>{t('library.batch.clear')}</button></div>
  {/if}
  <div class="table-wrap"><table><thead><tr>
    <th></th>
    <th><input type="checkbox" checked={tracks.length && selected.size===tracks.length} on:change={selectAll}></th>
    <th class="sortable" class:sorted={sort==='artist'} on:click={()=>handleSort('artist')}>{t('library.table.artist')} <span class="sort-arrow">{sort==='artist' ? (order==='asc'?'▲':'▼') : ''}</span></th>
    <th class="sortable" class:sorted={sort==='title'} on:click={()=>handleSort('title')}>{t('library.table.title')} <span class="sort-arrow">{sort==='title' ? (order==='asc'?'▲':'▼') : ''}</span></th>
    <th class="sortable" class:sorted={sort==='album'} on:click={()=>handleSort('album')}>{t('library.table.album')} <span class="sort-arrow">{sort==='album' ? (order==='asc'?'▲':'▼') : ''}</span></th>
    <th class="sortable" class:sorted={sort==='duration'} on:click={()=>handleSort('duration')}>{t('library.table.duration')} <span class="sort-arrow">{sort==='duration' ? (order==='asc'?'▲':'▼') : ''}</span></th>
    <th class="sortable" class:sorted={sort==='size'} on:click={()=>handleSort('size')}>{t('library.table.size')} <span class="sort-arrow">{sort==='size' ? (order==='asc'?'▲':'▼') : ''}</span></th>
    <th class="sortable" class:sorted={sort==='created'} on:click={()=>handleSort('created')}>{t('library.table.created')} <span class="sort-arrow">{sort==='created' ? (order==='asc'?'▲':'▼') : ''}</span></th>
    <th></th>
  </tr></thead>
  <tbody>
    {#each tracks as tr}
      <tr class:row-playing={currentId===tr.id}><td><button class="btn-play" class:playing={currentId===tr.id} on:click={()=> currentId===tr.id ? togglePlay() : playTrack(tr)}>{currentId===tr.id ? (isPlayingVal ? '⏸' : '▶') : '▶'}</button></td>
        <td><input type="checkbox" checked={selected.has(tr.id)} on:change={()=>toggleSelect(tr.id)}></td>
        <td>{tr.artist}</td><td>{tr.title}</td><td>{tr.album}</td>
        <td>{fmtDur(tr.duration)}</td><td>{fmtSize(tr.file_size)}</td><td>{fmtTime(tr.created_at)}</td>
        <td>
          <div class="dd-wrap">
            <button class="btn-dd" on:click={(e)=>{ const w=e.currentTarget.parentElement; w.classList.toggle('open'); const close=(ev)=>{ if(!w.contains(ev.target)){ w.classList.remove('open'); document.removeEventListener('click', close); }}; setTimeout(()=> document.addEventListener('click', close), 0); }}>⋮</button>
            <div class="dd-menu">
              <button class="dd-item" on:click={()=>playTrack(tr)}>{t('library.actions.download')}</button>
              <button class="dd-item" on:click={()=>openEdit(tr)}>{t('library.actions.edit')}</button>
              <button class="dd-item" on:click={()=>reloadTrack(tr.id)}>{t('library.actions.redownload')}</button>
              <button class="dd-item dd-danger" on:click={()=>delTrack(tr.id)}>{t('library.actions.delete')}</button>
            </div>
          </div>
        </td></tr>
    {/each}
  </tbody></table></div>
  <div bind:this={sentinelEl} class="scroll-sentinel"></div>
  {#if loading}<div class="loading"><div class="spinner"></div> {t('common.loading')}</div>{/if}

</main>
{#if showEdit}
<div class="modal" on:click|self={()=> showEdit=false}><div class="modal-content">
  <h3>{t('library.modal.editTitle')}</h3>
  <label>{t('library.modal.artist')} <input type="text" bind:value={editArtist}></label>
  <label>{t('library.modal.title')} <input type="text" bind:value={editTitle}></label>
  <label>{t('library.modal.album')} <input type="text" bind:value={editAlbum}></label>
  <div class="btn-row"><button class="btn-primary" on:click={saveEdit}>{t('library.modal.save')}</button><button on:click={()=> showEdit=false}>{t('library.modal.cancel')}</button></div>
</div></div>
{/if}
{#if showUpload}
<div class="modal" on:click|self={()=> showUpload=false}><div class="modal-content">
  <h3>{t('library.modal.uploadTitle')}</h3>
  <input type="file" accept="audio/*,.opus,.mp3,.m4a,.ogg,.webm,.flac" on:change={(e)=> uploadFile=e.target.files[0]}>
  <label>{t('library.modal.artist')} <input type="text" bind:value={uploadArtist} placeholder={t('library.modal.artistPlaceholder')}></label>
  <label>{t('library.modal.title')} <input type="text" bind:value={uploadTitle} placeholder={t('library.modal.titlePlaceholder')}></label>
  <label>{t('library.modal.album')} <input type="text" bind:value={uploadAlbum} placeholder={t('library.modal.albumPlaceholder')}></label>
  <div class="btn-row"><button class="btn-primary" on:click={doUpload}>{t('library.modal.upload')}</button><button on:click={()=> showUpload=false}>{t('library.modal.cancel')}</button></div>
</div></div>
{/if}

