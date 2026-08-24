// CSRF protection: every mutating same-origin request must carry the
// csrf_token cookie value as the X-CSRF-Token header (double-submit).
// Wrapping fetch here means all page scripts get it automatically.
(function () {
  function getCookie(name) {
    const m = document.cookie.match('(?:^|;\\s*)' + name + '=([^;]*)');
    return m ? decodeURIComponent(m[1]) : '';
  }

  const origFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const method = ((init && init.method) || 'GET').toUpperCase();
    if (method === 'GET' || method === 'HEAD' || method === 'OPTIONS') {
      return origFetch(input, init);
    }
    const token = getCookie('csrf_token');
    if (!token) return origFetch(input, init);
    // Clone init + headers so we never mutate the caller's object.
    const opts = Object.assign({}, init);
    opts.headers = Object.assign({}, init && init.headers, { 'X-CSRF-Token': token });
    return origFetch(input, opts);
  };
})();
