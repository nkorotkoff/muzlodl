<script>

  import { api } from '../lib/api.js';
  let username=''; let password=''; let confirm=''; let err='';
  async function doSetup(){
    if(!username.trim()){ err='username required'; return; }
    if(password.length < 8){ err='password must be at least 8 chars'; return; }
    if(password !== confirm){ err='passwords do not match'; return; }
    try{ await api('/api/setup', { method:'POST', body: JSON.stringify({username, password})}); window.location.hash='#/'; }catch(e){ err=e.message; }
  }

</script>


<main style="max-width:360px;margin:4rem auto"><div class="card"><h3>Setup — create admin</h3>
  <label>Username <input type="text" bind:value={username}></label>
  <label>Password <input type="password" bind:value={password}></label>
  <label>Confirm <input type="password" bind:value={confirm}></label>
  {#if err}<p style="color:#ff7b72">{err}</p>{/if}
  <button class="btn-primary" on:click={doSetup}>Create admin</button>
</div></main>

