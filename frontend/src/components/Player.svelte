<script>
  import { currentId, queue, queueIndex, isPlaying, currentTime, duration, volume, shuffle, bindAudio, next, prev, togglePlay, seekTo, setVolume, closePlayer, toggleShuffle } from '../lib/player.js';
  import { t } from '../lib/i18n.js';
  let audioEl;
  import { onMount } from 'svelte';
  onMount(()=> bindAudio(audioEl));
  function fmt(s){ if(!isFinite(s) || s<=0) return '0:00'; const m=Math.floor(s/60), sec=Math.floor(s%60); return m+':'+String(sec).padStart(2,'0'); }
  function onSeek(e){
    const rect=e.currentTarget.getBoundingClientRect();
    const pct=Math.min(1, Math.max(0, (e.clientX-rect.left)/rect.width));
    seekTo(pct);
  }
  $: cur = ($queue[$queueIndex] || {});
  $: pct = $duration ? ($currentTime/$duration*100) : 0;
</script>

<audio bind:this={audioEl} preload="none" style="display:none"></audio>
{#if $currentId}
<div id="player">
  <div class="player-inner">
    <button class="player-btn" on:click={prev} title={t('player.prev')}>⏮</button>
    <button class="player-btn" on:click={togglePlay} title={$isPlaying ? t('player.pause') : t('player.play')}>{$isPlaying ? '⏸' : '▶'}</button>
    <button class="player-btn" on:click={next} title={t('player.next')}>⏭</button>
    <button class="player-btn" on:click={toggleShuffle} title={`${t('player.shuffle')}: ${$shuffle ? 'ON' : 'OFF'}`} class:shuffle-active={$shuffle} aria-pressed={$shuffle}>{$shuffle ? '🔀✓' : '🔀'}</button>
    <div class="player-info">
      <span class="player-artist">{cur.artist || ''}</span>
      <span class="player-title">{cur.title || ''}</span>
      <span class="player-queue-info">{$queue.length ? `${$queueIndex+1} / ${$queue.length}` : ''}</span>
    </div>
    <div class="player-progress-wrap">
      <span class="player-time">{fmt($currentTime)}</span>
      <div class="player-progress" on:click={onSeek}>
        <div class="player-progress-track"><div class="player-progress-fill" style:width={pct+'%'}></div></div>
      </div>
      <span class="player-time">{fmt($duration)}</span>
    </div>
    <div class="player-volume"><span class="volume-icon">{$volume==0 ? '🔇' : $volume<0.5 ? '🔈' : '🔊'}</span><input type="range" min="0" max="1" step="0.05" value={$volume} on:input={(e)=> setVolume(parseFloat(e.target.value))}></div>
    <button class="player-btn" on:click={closePlayer} title={t('player.close')}>✕</button>
  </div>
</div>
{/if}

<style>
  .shuffle-active{ color: var(--accent) !important; background: var(--accent-soft) !important; border: 1px solid var(--accent) !important; border-radius: 6px; }
  .player-btn[aria-pressed="true"]{ position: relative; }
</style>
