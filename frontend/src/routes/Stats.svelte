<script>

  import { t } from '../lib/i18n.js';
  import { api } from '../lib/api.js';
  import { onMount } from 'svelte';
  let data = { summary:{total_plays:0, unique_tracks:0, last_7_days:0}, top_tracks:[], top_artists:[], recent:[] };
  onMount(async ()=>{ try{ data = await api('/api/stats'); }catch(e){} });

</script>


<main>
  <header class="page-header"><h1>{t('stats.title')}</h1><span class="stats">{t('stats.summary', {total: data.summary.total_plays, unique: data.summary.unique_tracks, week: data.summary.last_7_days})}</span></header>
  <div class="stats-grid"><div class="card"><h3>{t('stats.topTracks')}</h3>{#each data.top_tracks as r, i}<div class="stat-row"><span class="stat-rank">{i+1}</span><span class="stat-info"><span class="stat-title">{r.artist} — {r.title}</span></span><span class="stat-count">{r.plays}</span></div>{/each}{#if !data.top_tracks.length}<p class="hint">{t('stats.noPlaysShort')}</p>{/if}</div>
    <div class="card"><h3>{t('stats.topArtists')}</h3>{#each data.top_artists as r, i}<div class="stat-row"><span class="stat-rank">{i+1}</span><span class="stat-info"><span class="stat-title">{r.artist}</span></span><span class="stat-count">{r.plays}</span></div>{/each}</div></div>
  <div class="card" style="margin-top:1rem"><h3>{t('stats.recentlyPlayed')}</h3>{#each data.recent as r}<div class="stat-row"><span class="stat-info"><span class="stat-title">{r.artist} — {r.title}</span><span class="stat-sub">{r.played_at}</span></span></div>{/each}{#if !data.recent.length}<p class="hint">{t('stats.noPlays')}</p>{/if}</div>
</main>

