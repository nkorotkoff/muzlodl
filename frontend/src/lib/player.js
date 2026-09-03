
import { writable, get } from 'svelte/store';
import { api } from './api.js';

const KEY='music-loader-player';

export const currentId = writable(null);
export const queue = writable([]);
export const queueIndex = writable(-1);
export const isPlaying = writable(false);
export const currentTime = writable(0);
export const duration = writable(0);
export const volume = writable( (()=>{ try{ const v=localStorage.getItem('player-volume'); return v!==null? parseFloat(v):0.8; }catch(e){ return 0.8; }})() );
export const shuffle = writable( (()=>{ try{ return localStorage.getItem('player-shuffle')==='1'; }catch(e){ return false; }})() );
let shuffleOrder=[]; let shufflePos=0;
function buildShuffle(q, startIdx){
  const n=q.length;
  const idxs=listShuffle([...Array(n).keys()]);
  // ensure current track first
  const pos=idxs.indexOf(startIdx);
  if(pos>0){ idxs.splice(pos,1); idxs.unshift(startIdx); }
  shuffleOrder=idxs; shufflePos=0;
}
function listShuffle(a){ for(let i=a.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1)); [a[i],a[j]]=[a[j],a[i]]; } return a; }

let audio = null;
export function bindAudio(el){
  audio = el;
  if(!audio) return;
  try{ audio.volume = get(volume); }catch(e){}
  audio.addEventListener('timeupdate', ()=>{ currentTime.set(audio.currentTime); duration.set(audio.duration||0); });
  audio.addEventListener('play', ()=> isPlaying.set(true));
  audio.addEventListener('pause', ()=> isPlaying.set(false));
  audio.addEventListener('loadedmetadata', ()=> duration.set(audio.duration||0));
  audio.addEventListener('ended', ()=> next());
  audio.addEventListener('volumechange', ()=>{ volume.set(audio.volume); try{ localStorage.setItem('player-volume', String(audio.volume)); }catch(e){} });
  // restore if needed
  restore();
  // persist interval
  setInterval(save, 1500);
  window.addEventListener('beforeunload', save);
}

function save(){
  try{
    const id=get(currentId); if(!id || !audio) return;
    const q=get(queue); const qi=get(queueIndex);
    const tr = (q[qi] || {});
    localStorage.setItem(KEY, JSON.stringify({ id, artist: tr.artist||'', title: tr.title||'', time: audio.currentTime||0, volume: get(volume), queue: q, queueIndex: qi, shuffle: get(shuffle), shuffleOrder, shufflePos }));
  }catch(e){}
}

function restore(){
  try{
    const raw=localStorage.getItem(KEY); if(!raw) return;
    const s=JSON.parse(raw); if(!s || !s.id) return;
    if(Array.isArray(s.queue) && s.queue.length){ queue.set(s.queue); queueIndex.set(typeof s.queueIndex==='number'? s.queueIndex : 0); }
    currentId.set(s.id);
    if(typeof s.volume==='number'){ volume.set(s.volume); if(audio) audio.volume=s.volume; }
    if(typeof s.shuffle==='boolean'){ shuffle.set(s.shuffle); } else if(s.shuffle) shuffle.set(true);
    if(Array.isArray(s.shuffleOrder)){ shuffleOrder=s.shuffleOrder; shufflePos=typeof s.shufflePos==='number'? s.shufflePos:0; }
    if(!audio) return;
    const seek = typeof s.time==='number'? s.time:0;
    audio.src = `/api/library/${s.id}/stream`;
    const onMeta=()=>{ try{ if(seek>0 && seek < (audio.duration|| 1e9)) audio.currentTime=seek; }catch(e){} audio.removeEventListener('loadedmetadata', onMeta); };
    audio.addEventListener('loadedmetadata', onMeta);
  }catch(e){}
}

export async function ensureQueue(trackId, fetchFn){
  const q=get(queue);
  let idx=q.findIndex(x=> x.id===trackId);
  if(idx>=0){ queueIndex.set(idx); return; }
  // fetchFn should be (params)=> api result, we build full queue
  const all=[]; let off=0; while(true){
    const page=await fetchFn({ offset: off, limit:500 });
    all.push(...(page.tracks||[]).filter(x=> x.file_path));
    if((page.tracks||[]).length<500 || all.length >= (page.total||0)) break;
    off+=500;
  }
  const newQ = all.map(x=> ({id:x.id, artist:x.artist, title:x.title}));
  queue.set(newQ);
  let qi=newQ.findIndex(x=> x.id===trackId);
  if(qi<0) qi=0;
  queueIndex.set(qi);
}

export async function playTrack(tr, fetchFn){
  if(fetchFn) await ensureQueue(tr.id, fetchFn);
  currentId.set(tr.id);
  if(!audio) return;
  audio.src = `/api/library/${tr.id}/stream`;
  audio.play();
  setTimeout(save,200);
}
export function next(){
  const q=get(queue); if(!q.length) return;
  if(get(shuffle)){
    if(!shuffleOrder.length) buildShuffle(q, get(queueIndex));
    shufflePos=(shufflePos+1)%shuffleOrder.length; const qi=shuffleOrder[shufflePos]; queueIndex.set(qi);
    const nxt=q[qi]; if(nxt && audio){ currentId.set(nxt.id); audio.src=`/api/library/${nxt.id}/stream`; audio.play(); setTimeout(save,200); }
    return;
  }
  let qi=get(queueIndex);
  qi=(qi+1)%q.length; queueIndex.set(qi);
  const nxt=q[qi]; if(nxt && audio){ currentId.set(nxt.id); audio.src=`/api/library/${nxt.id}/stream`; audio.play(); setTimeout(save,200); }
}
export function prev(){
  const q=get(queue); if(!q.length) return;
  if(get(shuffle)){
    if(!shuffleOrder.length) buildShuffle(q, get(queueIndex));
    shufflePos=(shufflePos-1+shuffleOrder.length)%shuffleOrder.length; const qi=shuffleOrder[shufflePos]; queueIndex.set(qi);
    const prv=q[qi]; if(prv && audio){ currentId.set(prv.id); audio.src=`/api/library/${prv.id}/stream`; audio.play(); setTimeout(save,200); }
    return;
  }
  let qi=get(queueIndex);
  qi=(qi-1+q.length)%q.length; queueIndex.set(qi);
  const prv=q[qi]; if(prv && audio){ currentId.set(prv.id); audio.src=`/api/library/${prv.id}/stream`; audio.play(); setTimeout(save,200); }
}
export function togglePlay(){ if(!audio) return; if(audio.paused) audio.play(); else audio.pause(); }
export function seekTo(pct){
  if(!audio) return;
  const d=get(duration) || audio.duration || 0;
  if(!d) return;
  audio.currentTime = Math.max(0, Math.min(d, pct*d));
  setTimeout(save,100);
}
export function setVolume(v){
  volume.set(v);
  if(audio) audio.volume=v;
  try{ localStorage.setItem('player-volume', String(v)); }catch(e){}
  setTimeout(save,100);
}
export function toggleShuffle(){
  const on=!get(shuffle);
  shuffle.set(on);
  try{ localStorage.setItem('player-shuffle', on?'1':'0'); }catch(e){}
  if(on){
    const q=get(queue); const qi=get(queueIndex);
    buildShuffle(q, qi);
  } else {
    shuffleOrder=[]; shufflePos=0;
  }
  setTimeout(save,100);
}
export function closePlayer(){ currentId.set(null); if(audio) audio.pause(); try{ localStorage.removeItem(KEY); }catch(e){} }
