<script>

  import { t } from '../lib/i18n.js';
  import { api } from '../lib/api.js';
  import { onMount } from 'svelte';
  let pending = [];
  try{ const s = localStorage.getItem('music-loader-pending'); if(s) pending = JSON.parse(s); }catch(e){}
  function save(){ try{ localStorage.setItem('music-loader-pending', JSON.stringify(pending)); }catch(e){} }
  let bulk=''; let artist=''; let title='';
  let quality='128'; let parallel=4; let max_path_len=0; let enrich=true; let impName='';
  let progress = null; let jobId=null;
  function addOne(){ if(!title.trim()) return; pending = [...pending, {artist: artist.trim(), title: title.trim(), album:''}]; save(); title=''; }
  function addBulk(){
    if(!bulk.trim()) return;
    const lines = bulk.split('\n').map(l=>l.trim()).filter(Boolean);
    const head = lines[0]?.toLowerCase()||'';
    let add=[];
    if(head.includes('artist') && head.includes('title')){
      const header = lines[0].split(',').map(h=>h.trim().toLowerCase());
      const aIdx=header.indexOf('artist'), tIdx=header.indexOf('title'), alIdx=header.indexOf('album');
      for(let i=1;i<lines.length;i++){ const cells=lines[i].split(','); if(tIdx>=0 && cells[tIdx]?.trim()) add.push({artist: aIdx>=0?(cells[aIdx]||'').trim():'', title:(cells[tIdx]||'').trim(), album: alIdx>=0?(cells[alIdx]||'').trim():''}); }
    } else if(bulk.trim().startsWith('[') || bulk.trim().startsWith('{')){
      try{ const data=JSON.parse(bulk); const arr=Array.isArray(data)?data:(data.tracks||[]); add = arr.map(x=>({artist:(x.artist||'').trim(), title:(x.title||'').trim(), album:(x.album||'').trim()})).filter(x=>x.title);}catch(e){}
    } else {
      for(const line of lines){ if(!line || line.startsWith('#')) continue; const parts=line.split(/\s+-\s+/); if(parts.length>=2) add.push({artist:parts[0], title:parts[1], album:parts.slice(2).join(' - ')}); else add.push({artist:'', title:line, album:''}); }
    }
    if(add.length){ pending=[...pending, ...add]; save(); bulk=''; }
  }
  function removeIdx(i){ pending = pending.filter((_,j)=>j!==i); save(); }
  function clearQueue(){ pending=[]; save(); }
  let dragIdx=null;
  async function startDownload(){
    if(!pending.length) return;
    const resp = await api('/api/download', { method:'POST', body: JSON.stringify({ source:'text', tracks: pending, options:{ quality, parallel, max_path_len, enrich, name: impName } }) });
    jobId = resp.job_id; pending=[]; save();
    const iv = setInterval(async ()=>{
      try{ const s = await api('/api/download/'+jobId); const p=s.progress||{ok:0,failed:0,total:0}; progress=`${p.ok} ok, ${p.failed} failed / ${p.total}`; if(s.done||s.cancelled){ clearInterval(iv); progress = s.cancelled?'Cancelled':'✅ Complete'; } }catch(e){ clearInterval(iv); }
    }, 1000);
  }
  async function handleDrop(e){
    e.preventDefault();
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if(f){ const text = await f.text(); bulk = text; addBulk(); return; }
    const txt = e.dataTransfer.getData('text/plain'); if(txt && txt.trim()){ bulk = txt; addBulk(); }
  }
  let recent=[]; let recentTracks={}; let expanded=new Set(); let recentMeta={}; // id -> {total, offset, loading}
  async function loadRecent(){ try{ const d= await api('/api/imports?limit=20'); recent = d.sessions||[]; }catch(e){} }
  async function toggleRecent(id){
    if(expanded.has(id)){ expanded.delete(id); expanded=new Set(expanded); return; }
    expanded.add(id); expanded=new Set(expanded);
    recentMeta[id]={ total:0, offset:0, loading:true };
    try{
      const d= await api(`/api/imports/${id}?limit=100&offset=0`);
      recentTracks[id]=d.tracks||[]; recentTracks={...recentTracks};
      recentMeta[id]={ total: d.total ?? recentTracks[id].length, offset: recentTracks[id].length, loading:false }; recentMeta={...recentMeta};
    }catch(e){ recentMeta[id].loading=false; recentMeta={...recentMeta}; }
  }
  async function loadMoreRecent(id){
    const meta=recentMeta[id]; if(!meta || meta.loading || meta.offset >= meta.total) return;
    meta.loading=true; recentMeta={...recentMeta};
    try{
      const d= await api(`/api/imports/${id}?limit=100&offset=${meta.offset}`);
      const add=d.tracks||[];
      recentTracks[id]=[...(recentTracks[id]||[]), ...add]; recentTracks={...recentTracks};
      recentMeta[id]={ total: d.total ?? meta.total, offset: meta.offset + add.length, loading:false }; recentMeta={...recentMeta};
    }catch(e){ meta.loading=false; recentMeta={...recentMeta}; }
  }
  async function retryTrack(id){ await api(`/api/library/${id}/retry`, {method:'POST'}); }
  onMount(loadRecent);

</script>


<main>
  <header class="page-header"><h1>{t('import.title')}</h1></header>
  <div class="drop-zone" tabindex="0" role="button" on:click={()=>document.getElementById('file-input').click()} on:dragover|preventDefault on:drop={handleDrop} on:keydown={(e)=>{ if(e.key==='Enter'||e.key===' ') document.getElementById('file-input').click(); }}>
    <div class="drop-icon">⤓</div>
    <div><div class="drop-title">{t('import.dropTitle')}</div><div class="drop-hint">{t('import.dropHint')}</div></div>
    <input type="file" id="file-input" accept=".csv,.json,.txt,text/*" hidden on:change={async (e)=>{ const f=e.target.files[0]; if(f){ const tx=await f.text(); bulk=tx; addBulk(); e.target.value=''; }}}>
  </div>
  <div class="card" style="margin-top:1rem">
    <div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">{t('import.queue')}</h3><span class="hint">{pending.length ? t('import.trackList', {n: pending.length}) : ''}</span></div>
    <label class="hint" style="margin:.75rem 0 .3rem">{t('import.bulkLabel')}</label>
    <textarea rows="3" placeholder={t('import.bulkPlaceholder')} bind:value={bulk}></textarea>
    <div class="btn-row" style="margin-top:.5rem"><button on:click={addBulk}>{t('import.parse')}</button><button class="btn-action" on:click={()=> bulk=''}>{t('common.clear')}</button><span class="hint">{t('import.bulkHint')}</span></div>
    <div class="track-input-row" style="margin-top:1rem"><input type="text" placeholder={t('import.artist')} bind:value={artist}><input type="text" placeholder={t('import.titleLabel')} bind:value={title}><button class="btn-primary" style="margin-top:0" on:click={addOne}>{t('import.addBtn')}</button></div>
    {#if pending.length}
      <div class="table-wrap" style="margin-top:.75rem"><table><thead><tr><th></th><th>{t('library.table.artist')}</th><th>{t('library.table.title')}</th><th>{t('library.table.album')}</th><th></th></tr></thead>
      <tbody>
        {#each pending as p, i}
          <tr draggable="true" on:dragstart={()=> dragIdx=i} on:dragover|preventDefault on:drop={()=>{ if(dragIdx!==null && dragIdx!==i){ const mv=pending[dragIdx]; pending.splice(dragIdx,1); pending.splice(i,0,mv); pending=[...pending]; save(); } dragIdx=null; }}>
            <td class="drag-handle">⋮⋮</td><td>{p.artist || '⚠ no artist'}</td><td>{p.title}</td><td>{p.album}</td><td><button class="btn-del" on:click={()=>removeIdx(i)}>✕</button></td>
          </tr>
        {/each}
      </tbody></table></div>
      <div style="display:flex;gap:.5rem;margin-top:.6rem"><button class="btn-action" on:click={clearQueue}>{t('import.clearQueue')}</button></div>
    {/if}
    <details style="margin-top:1rem"><summary>{t('import.advanced')}</summary>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.75rem;margin-top:.75rem">
        <label>{t('import.quality')} <select bind:value={quality}><option value="64">64</option><option value="128">128</option><option value="192">192</option></select></label>
        <label>{t('import.parallel')} <input type="number" bind:value={parallel} min="1" max="16"></label>
        <label>{t('import.maxPath')} <input type="number" bind:value={max_path_len}></label>
        <label>{t('import.importName')} <input type="text" bind:value={impName} placeholder={t('import.importNamePlaceholder')}></label>
        <label><input type="checkbox" bind:checked={enrich}> {t('import.enrich')}</label>
      </div>
    </details>
    <div style="position:sticky;bottom:0;background:var(--bg-elev);padding:.75rem 0 .25rem;margin-top:1rem;border-top:1px solid var(--border-soft);display:flex;gap:.75rem;align-items:center">
      <button class="btn-primary" on:click={startDownload} disabled={!pending.length}>{pending.length ? t('import.downloadCount', {n: pending.length}) : t('import.download')}</button>
      {#if progress}<span class="hint">{progress}</span>{/if}
    </div>
  </div>
  <details style="margin-top:1rem" open><summary>{t('import.recentImports')} ({recent.length})</summary>
    <div style="margin-top:.5rem">{#each recent as r}<div class="recent-item"><div class="recent-row" on:click={()=>toggleRecent(r.id)} style="cursor:pointer"><span class="recent-label">{r.source_name || r.source}</span><span class="recent-meta">{r.created_at} · {r.downloaded}/{r.total} {r.failed? '('+r.failed+' failed)':''}</span><span class="recent-arrow">{expanded.has(r.id) ? '▾' : '▸'}</span></div>{#if expanded.has(r.id)}<div class="recent-body"><table><tbody>{#each (recentTracks[r.id]||[]) as tr}<tr><td>{tr.artist}</td><td>{tr.title}</td><td>{tr.status} {#if tr.status==='failed'}<button class="btn-retry" on:click|stopPropagation={()=>retryTrack(tr.id)}>↻</button>{/if}</td></tr>{/each}</tbody></table>
      {#if (recentMeta[r.id]?.total ?? 0) > (recentMeta[r.id]?.offset ?? 0)}
        <button class="btn-action" style="margin-top:.5rem" on:click={()=>loadMoreRecent(r.id)} disabled={recentMeta[r.id]?.loading}>{recentMeta[r.id]?.loading ? t('infinite.loading') : t('infinite.more', {remaining: recentMeta[r.id].total - recentMeta[r.id].offset, total: recentMeta[r.id].total})}</button>
      {:else if (recentTracks[r.id]?.length ?? 0) >= 100}
        <p class="hint" style="margin-top:.5rem">{t('infinite.allShown', {total: recentMeta[r.id]?.total ?? recentTracks[r.id].length})}</p>
      {/if}
      </div>{/if}</div>{/each} {#if !recent.length}<p class="hint">{t('import.noImports')}</p>{/if}</div>
  </details>
</main>

