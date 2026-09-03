<script>

  import { t } from '../lib/i18n.js';
  import { api } from '../lib/api.js';
  import { onMount } from 'svelte';
  let tab='general'; let saveText=''; let saveTimer=null;
  let s={ admin_username:'', acoustid_api_key:'', acoustid_verify:false, acoustid_min_score:'0.5', quality:'128', parallel:'4', max_path_len:'0', enrich:'true' };
  let sources=[]; let sourcesAuto=true;
  let cloud={ backend:'yandex', login:'', password:'', root:'music' };
  let cloudStatus='...'; let cloudMsg='';
  let doctor=[]; let doctorLoading=false;
  let reencodeBitrate='128'; let reencodeProgress='';
  let maintMsg='';
  function switchTab(n){ tab=n; try{localStorage.setItem('settings-tab', n);}catch(e){} }
  function scheduleSave(){
    saveText=t('settings.saving'); clearTimeout(saveTimer);
    saveTimer=setTimeout(async()=>{
      try{
        const payload={ ...s, sources: JSON.stringify(sources), sources_auto: sourcesAuto ? 'true':'false', acoustid_verify: s.acoustid_verify ? 'true':'false' };
        if(s.password) payload.password=s.password; else delete payload.password;
        await api('/api/settings', {method:'PUT', body: JSON.stringify(payload)});
        saveText=t('settings.saved'); setTimeout(()=> saveText='', 1200);
      }catch(e){ saveText=''; }
    }, 400);
  }
  async function load(){
    try{
      const d=await api('/api/settings');
      Object.assign(s, { admin_username: d.admin_username||'', acoustid_api_key: d.acoustid_api_key||'', acoustid_verify: d.acoustid_verify==='true', acoustid_min_score: d.acoustid_min_score||'0.5', quality: d.quality||'128', parallel: d.parallel||'4', max_path_len: d.max_path_len||'0', enrich: d.enrich||'true' });
      s.password='';
      sourcesAuto = (d.sources_auto !== 'false');
      try{ sources = JSON.parse(d.sources||'[]'); if(!Array.isArray(sources)) sources=[]; }catch(e){ sources=[]; }
    }catch(e){}
    try{ const c=await api('/api/cloud/status'); cloudStatus = c.configured ? `${c.backend} ${c.reachable?'✅':''} /${c.root||'music'}` : t('settings.cloudNotConfigured'); }catch(e){ cloudStatus=''; }
  }
  async function runDoctor(){
    doctorLoading=true; doctor=[];
    try{
      const d=await api('/api/doctor'); doctor = d.results || d.sources || d || [];
      if(Array.isArray(d)) doctor=d;
    }catch(e){} doctorLoading=false;
  }
  async function saveCloud(){
    cloudMsg=t('settings.cloudChecking');
    try{
      await api('/api/cloud/config', {method:'POST', body: JSON.stringify(cloud)});
      cloudMsg='✅ '+t('common.configured'); load();
    }catch(e){ cloudMsg='❌ '+(e.message||'error'); }
  }
  async function clearCloud(){ await api('/api/cloud/config', {method:'DELETE'}); cloudMsg=t('common.cleared'); load(); }
  async function uploadCloud(){ maintMsg=t('settings.uploading'); try{ const r=await api('/api/cloud/upload', {method:'POST'}); const iv=setInterval(async()=>{ const st=await api('/api/download/'+r.job_id); if(st.done){ clearInterval(iv); maintMsg='✅ '+t('common.uploaded'); } }, 1000); }catch(e){ maintMsg='❌ '+e.message; } }
  async function startReencode(){ maintMsg=t('settings.starting'); try{ const r=await api('/api/library/reencode', {method:'POST', body: JSON.stringify({bitrate: parseInt(reencodeBitrate)})}); const iv=setInterval(async()=>{ const st=await api('/api/download/'+r.job_id); const p=st.progress||{ok:0,total:0}; reencodeProgress = `${p.ok}/${p.total}`; if(st.done){ clearInterval(iv); reencodeProgress='✅ Done'; } }, 1000); }catch(e){ maintMsg='❌ '+e.message; } }
  async function retryFailed(){ maintMsg=t('settings.retrying'); try{ const r=await api('/api/library/retry-failed', {method:'POST'}); maintMsg=t('settings.retryingCount', {n: r.total}); }catch(e){ maintMsg='❌ '+e.message; } }
  onMount(()=>{ const saved=(()=>{try{return localStorage.getItem('settings-tab');}catch(e){return null}})(); if(saved) tab=saved; load(); });

</script>


<main>
  <header class="page-header"><h1>{t('settings.title')}</h1><span class="hint" style="min-width:90px;text-align:right">{saveText}</span></header>
  <div class="settings-layout">
    <nav class="settings-tabs" role="tablist">
      <button role="tab" class:tab-active={tab==='general'} on:click={()=>switchTab('general')}>{t('settings.tab.general')}</button>
      <button role="tab" class:tab-active={tab==='sources'} on:click={()=>switchTab('sources')}>{t('settings.tab.sources')}</button>
      <button role="tab" class:tab-active={tab==='audio'} on:click={()=>switchTab('audio')}>{t('settings.tab.audio')}</button>
      <button role="tab" class:tab-active={tab==='cloud'} on:click={()=>switchTab('cloud')}>{t('settings.tab.cloud')}</button>
      <button role="tab" class:tab-active={tab==='system'} on:click={()=>switchTab('system')}>{t('settings.tab.system')}</button>
    </nav>
    <div class="settings-panels">
      {#if tab==='general'}
        <section class="settings-panel"><div class="card"><h3>{t('settings.language')}</h3>
          <select value={localStorage.getItem('lang')||'en'} on:change={(e)=>{ localStorage.setItem('lang', e.target.value); location.reload(); }}><option value="en">English</option><option value="ru">Русский</option></select>
        </div></section>
      {:else if tab==='sources'}
        <section class="settings-panel"><div class="card"><h3>{t('settings.sources')}</h3>
          <label><input type="checkbox" bind:checked={sourcesAuto} on:change={scheduleSave}> {t('settings.sourcesAuto')}</label>
          <p class="hint">{t('settings.sourcesAutoHint')}</p>
          {#if !sourcesAuto}
            <p class="hint">{t('settings.sourcesHint')}</p>
            <div id="sources-list">{#each sources as src, i}<div class="source-row"><span class="source-name">{src}</span>
                {#if i>0}<button on:click={()=>{ const a=[...sources]; [a[i-1],a[i]]=[a[i],a[i-1]]; sources=a; scheduleSave(); }}>↑</button>{/if}
                {#if i < sources.length-1}<button on:click={()=>{ const a=[...sources]; [a[i],a[i+1]]=[a[i+1],a[i]]; sources=a; scheduleSave(); }}>↓</button>{/if}
              </div>{/each}</div>
          {/if}
          <button on:click={runDoctor} style="margin-top:.75rem">{t('settings.testSources')}</button>
          {#if doctorLoading}<p class="hint">{t('settings.testing')}</p>{/if}
          {#if doctor.length}<div style="margin-top:.75rem">{#each doctor as d}<div style="font-size:.85rem">{d.name||d.source}: {d.status||d.available}</div>{/each}</div>{/if}
        </div></section>
      {:else if tab==='audio'}
        <section class="settings-panel">
          <div class="card"><h3>{t('settings.acoustid')}</h3><p class="hint">{t('settings.acoustidHint')}</p>
            <label>{t('settings.apiKey')} <input type="text" bind:value={s.acoustid_api_key} on:input={scheduleSave}></label>
            <label><input type="checkbox" bind:checked={s.acoustid_verify} on:change={scheduleSave}> {t('settings.verify')}</label>
            <label>{t('settings.minScore')} <input type="number" bind:value={s.acoustid_min_score} min="0" max="1" step="0.1" on:input={scheduleSave}></label>
          </div>
          <div class="card" style="margin-top:1rem"><h3>{t('settings.reencode')}</h3>
            <label>{t('settings.targetBitrate')} <select bind:value={reencodeBitrate}><option value="64">64</option><option value="96">96</option><option value="128">128</option></select></label>
            <button on:click={startReencode}>{t('settings.reencodeLibrary')}</button>
            {#if reencodeProgress}<p class="hint">{reencodeProgress}</p>{/if}
          </div>
        </section>
      {:else if tab==='cloud'}
        <section class="settings-panel"><div class="card"><h3>{t('settings.cloud')}</h3><div>{cloudStatus}</div>
          <div style="margin-top:.75rem">
            <select bind:value={cloud.backend}><option value="yandex">Yandex.Disk (WebDAV)</option><option value="yandex_rest">Yandex.Disk (REST)</option><option value="mailru">Cloud.Mail.ru</option></select>
            <label>{t('settings.login')} <input type="text" bind:value={cloud.login}></label>
            <label>{t('settings.appPassword')} <input type="password" bind:value={cloud.password}></label>
            <label>{t('settings.rootFolder')} <input type="text" bind:value={cloud.root}></label>
            <div class="btn-row"><button on:click={saveCloud}>{t('settings.saveTest')}</button><button class="btn-del" on:click={clearCloud}>{t('settings.remove')}</button></div>
            {#if cloudMsg}<p class="hint">{cloudMsg}</p>{/if}
          </div>
          <button on:click={uploadCloud} class="btn-primary" style="margin-top:.75rem">{t('settings.uploadLibrary')}</button>
        </div></section>
      {:else}
        <section class="settings-panel">
          <div class="card"><h3>{t('settings.maintenance')}</h3><div class="btn-row"><button on:click={retryFailed}>{t('settings.retryFailed')}</button></div>{#if maintMsg}<p class="hint">{maintMsg}</p>{/if}</div>
          <div class="card" style="margin-top:1rem"><h3>{t('settings.security')}</h3>
            <label>{t('settings.adminUsername')} <input type="text" bind:value={s.admin_username} on:input={scheduleSave}></label>
            <label>{t('settings.newPassword')} <input type="password" placeholder={t('settings.newPasswordPlaceholder')} on:change={(e)=>{ s.password=e.target.value; scheduleSave(); }}></label>
            <p class="hint">{t('settings.passwordHint')}</p>
          </div>
        </section>
      {/if}
    </div>
  </div>
</main>

