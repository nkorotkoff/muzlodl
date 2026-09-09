function doLogin() {
  const username = document.getElementById('login-username').value.trim();
  const pwd = document.getElementById('login-password').value;
  fetch('/api/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password: pwd}),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) { location.href = '/'; }
    else {
      const el = document.getElementById('login-error');
      el.textContent = d.error || 'Login failed';
      el.style.display = 'block';
    }
  })
  .catch(() => {
    const el = document.getElementById('login-error');
    el.textContent = 'Network error';
    el.style.display = 'block';
  });
}
document.getElementById('login-password').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
