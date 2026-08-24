function doSetup() {
  const username = document.getElementById('setup-username').value.trim();
  const password = document.getElementById('setup-password').value;
  const confirm = document.getElementById('setup-confirm').value;
  const el = document.getElementById('setup-error');
  const fail = msg => { el.textContent = msg; el.style.display = 'block'; };

  if (!username) return fail('Username is required');
  if (password.length < 8) return fail('Password must be at least 8 chars');
  if (password !== confirm) return fail('Passwords do not match');

  fetch('/api/setup', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password}),
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) { location.href = '/'; }
    else fail(d.error || 'Setup failed');
  })
  .catch(() => fail('Network error'));
}
document.getElementById('setup-confirm').addEventListener('keydown', e => { if (e.key === 'Enter') doSetup(); });
