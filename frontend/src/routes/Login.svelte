<script>

  import { api } from '../lib/api.js';
  let username=''; let password=''; let err='';
  async function doLogin(){
    err='';
    try{
      const d = await api('/api/login', { method:'POST', body: JSON.stringify({username, password})});
      if(d.ok) window.location.hash = '#/';
    }catch(e){ err = e.message; }
  }

</script>


<main style="max-width:360px;margin:4rem auto"><div class="card"><h3>Login</h3>
  <label>Username <input type="text" bind:value={username}></label>
  <label>Password <input type="password" bind:value={password} on:keydown={(e)=>{ if(e.key==='Enter') doLogin(); }}></label>
  {#if err}<p style="color:#ff7b72">{err}</p>{/if}
  <button class="btn-primary" on:click={doLogin}>Log in</button>
</div></main>

