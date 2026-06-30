/**
 * Spotify playlist scraper (v2)
 *
 * Usage:
 *   1. Open your Spotify playlist in the web player
 *      (https://open.spotify.com/playlist/...)
 *   2. Open DevTools console (F12 → Console)
 *   3. Paste this whole script, hit Enter
 *   4. Watch the progress log; auto-scrolls until the playlist ends
 *   5. CSV is copied to clipboard + downloaded as a file
 *
 * Output:
 *   - spotify-tracks.csv  (artist, title, album, year) — feed to music-loader
 *   - spotify-tracks.json (full metadata incl. track_id, duration, cover_url)
 *   - window.__spotifyTracks
 */
(async () => {
  'use strict';

  // ===== CONFIG =====
  const SCROLL_DELAY_MS  = 700;   // wait between scrolls
  const MAX_IDLE_SCROLLS = 6;     // stop after this many no-op scrolls
  const SCROLL_STEP_PX    = 1200;  // how far to scroll each tick

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const log = (...a) => console.log("[spotify]", ...a);

  // ===== FIND SCROLLABLE CONTAINER =====
  // Walk up from a real track row; first ancestor with overflowY=scroll/auto wins.
  const findScroller = () => {
    const row = document.querySelector('[data-testid="tracklist-row"]');
    if (!row) return null;
    let el = row.parentElement;
    while (el && el !== document.documentElement) {
      const s = getComputedStyle(el);
      if ((s.overflowY === 'scroll' || s.overflowY === 'auto' || s.overflow === 'scroll' || s.overflow === 'auto')
          && el.scrollHeight > el.clientHeight) {
        return el;
      }
      el = el.parentElement;
    }
    return null;
  };

  // ===== SCROLL ATTEMPT =====
  // Try several strategies. Spotify uses a custom scroller that sometimes
  // doesn't react to scrollTop / scrollBy, so we hit it from multiple angles.
  const tryScroll = (el) => {
    if (!el) return 0;
    const before = el.scrollTop;
    const max = el.scrollHeight - el.clientHeight;
    // 1) jump to absolute bottom
    try { el.scrollTop = el.scrollHeight; } catch {}
    // 2) also try relative scroll
    try { el.scrollBy(0, SCROLL_STEP_PX); } catch {}
    // 3) try scrolling the last visible row into view
    const rows = el.querySelectorAll('[data-testid="tracklist-row"]');
    if (rows.length) {
      try { rows[rows.length - 1].scrollIntoView({ block: 'end' }); } catch {}
    }
    return el.scrollTop - before;
  };

  // ===== EXTRACT ONE ROW =====
  const extractRow = (row) => {
    const link = row.querySelector('a[data-testid="internal-track-link"]');
    if (!link) return null;
    const trackHref = link.getAttribute('href') || '';
    const trackId = (trackHref.match(/\/track\/([A-Za-z0-9]+)/) || [])[1] || '';
    const title = (link.textContent || '').trim();

    const artistLinks = row.querySelectorAll('a[href*="/artist/"]');
    const artists = [...artistLinks]
      .map((a) => (a.textContent || '').trim())
      .filter(Boolean);

    const albumLink = row.querySelector('a[href*="/album/"]');
    const album = albumLink ? (albumLink.textContent || '').trim() : '';

    const img = row.querySelector('img[src*="scdn.co/image"]');
    const coverUrl = img ? img.getAttribute('src') : '';

    let duration = '';
    const cells = row.querySelectorAll('[role="gridcell"]');
    if (cells.length) {
      const lastCell = cells[cells.length - 1];
      for (const d of lastCell.querySelectorAll('div')) {
        const t = (d.textContent || '').trim();
        if (/^\d+:\d{2}(?::\d{2})?$/.test(t)) { duration = t; break; }
      }
    }

    let position = '';
    if (cells.length) {
      const firstSpan = cells[0].querySelector('span');
      if (firstSpan && /^\d+$/.test((firstSpan.textContent || '').trim())) {
        position = (firstSpan.textContent || '').trim();
      }
    }

    return {
      position,
      track_id: trackId,
      title,
      artists: artists.join(', '),
      artist_count: artists.length,
      album,
      duration,
      track_url: 'https://open.spotify.com' + trackHref,
      cover_url: coverUrl,
    };
  };

  // ===== MAIN =====
  const seen = new Map();
  const ingest = () => {
    document.querySelectorAll('[data-testid="tracklist-row"]').forEach((row) => {
      const t = extractRow(row);
      if (t && t.track_id) seen.set(t.track_id, t);
    });
  };
  ingest();
  log(`initial: ${seen.size} tracks visible`);

  const scroller = findScroller();
  if (scroller) {
    log(`scroller: <${scroller.tagName.toLowerCase()} class="${scroller.className}"> `
        + `scrollHeight=${scroller.scrollHeight} clientHeight=${scroller.clientHeight}`);
  } else {
    log('no scrollable ancestor found — will scroll the window instead');
  }

  let idle = 0;
  let totalTicks = 0;
  let lastSize = seen.size;

  while (idle < MAX_IDLE_SCROLLS) {
    totalTicks++;
    const delta = tryScroll(scroller);
    if (scroller && delta === 0 && scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) {
      // already at the bottom
      log(`tick ${totalTicks}: already at bottom (scrollTop=${scroller.scrollTop})`);
    } else {
      log(`tick ${totalTicks}: scrolled by ${delta}px, waiting ${SCROLL_DELAY_MS}ms...`);
    }
    await sleep(SCROLL_DELAY_MS);
    ingest();
    if (seen.size > lastSize) {
      log(`  +${seen.size - lastSize} new (total ${seen.size})`);
      lastSize = seen.size;
      idle = 0;
    } else {
      idle++;
      log(`  no new tracks (idle ${idle}/${MAX_IDLE_SCROLLS})`);
    }
  }

  // Final collect in case anything appeared during the last wait
  await sleep(SCROLL_DELAY_MS);
  ingest();

  const tracks = [...seen.values()].sort((a, b) =>
    (parseInt(a.position, 10) || 0) - (parseInt(b.position, 10) || 0)
  );
  log(`done: ${tracks.length} tracks total`);

  // ===== CSV (music-loader format) =====
  const esc = (s) => '"' + String(s).replace(/"/g, '""') + '"';
  const csv = ['artist,title,album,year']
    .concat(tracks.map((t) =>
      [esc(t.artists), esc(t.title), esc(t.album), ''].join(',')
    ))
    .join('\n');
  const json = JSON.stringify(tracks, null, 2);

  // ===== DOWNLOAD =====
  const download = (content, filename, mime = 'text/plain;charset=utf-8') => {
    try {
      const blob = new Blob([content], { type: mime });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1500);
    } catch (e) {
      console.error("[spotify] download failed:", e);
    }
  };

  try {
    await navigator.clipboard.writeText(csv);
    log('CSV copied to clipboard');
  } catch (e) {
    log(`clipboard blocked (${e.message}); use downloaded file`);
  }

  download(csv,  'spotify-tracks.csv');
  download(json, 'spotify-tracks.json');
  log('downloaded: spotify-tracks.csv, spotify-tracks.json');

  window.__spotifyTracks = tracks;
  log(`available as window.__spotifyTracks (${tracks.length} items)`);
})();
