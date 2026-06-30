# music-loader

[![CI](https://github.com/OWNER/music-loader/actions/workflows/tests.yml/badge.svg)](https://github.com/OWNER/music-loader/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)

Bulk music downloader with multi-source fallback. Each track is searched across
configurable sources (Archive.org → Openverse → Wikimedia Commons → Yandex Music
→ Audius → Bandcamp → SoundCloud → YouTube → Bilibili → Dailymotion → Jamendo);
the first source that returns a working file wins. iTunes Search runs as a
metadata enricher to fill in album, year, and cover art before audio search.
Downloaded library is laid out as `Artist/Album/Track.mp3` with full ID3 tags,
and can be uploaded to Yandex.Disk or Cloud.Mail.ru via WebDAV for offline
playback on a phone behind a network whitelist.

Built to be packaged into a single binary via PyInstaller.

## Features

- **Multiple audio sources** with automatic fallback
- **iTunes enricher** runs first to canonicalize metadata (album, year, cover, duration)
- **Bulk input**: CSV, JSON, plain text, or a Spotify playlist URL
- **Smart matching**: title/artist scoring, picks the best result, skips poor matches
- **ID3 tags + cover art** embedded automatically
- **Skips already-downloaded tracks**, so re-runs are fast
- **Uploads to Yandex.Disk / Cloud.Mail.ru** via WebDAV (works behind a strict whitelist)
- **Single binary** distribution via PyInstaller
- **No external services you don't control**: your scripts, your data, your cloud

## Install (development)

```bash
sudo apt install python3-pip ffmpeg        # ffmpeg is required for audio transcoding
pip install -r requirements.txt
# Optional sources:
pip install yandex-music
pip install spotdl
```

## Install (single binary)

```bash
./build.sh
cp dist/music-loader ~/.local/bin/
music-loader --help
```

The binary is self-contained (~80MB, includes Python + yt-dlp). It still calls
`ffmpeg` from the system — there's no way around that for audio transcoding.

## Configuration

Copy `.env.example` to `.env` and fill in credentials you want to use, or export
the variables directly. Cloud storage credentials are configured via
`music-loader cloud-setup` (not via env).

| Var | Used by |
|---|---|
| `YANDEX_TOKEN` | Yandex Music (full tracks; without it, 30s previews) |
| `JAMENDO_CLIENT_ID` | Jamendo (free CC) |
| `SPOTIFY_CLIENT_ID/SECRET` | Spotify URL import (via spotdl) |

**No-credential sources (always work without env vars):** YouTube, Bandcamp,
SoundCloud, Bilibili, Dailymotion (all via yt-dlp), Audius (free streaming),
Archive.org (public domain audio). iTunes Search (enricher) doesn't need a key
either.

```bash
export JAMENDO_CLIENT_ID="..."  # only if using Jamendo source
export YANDEX_TOKEN="..."       # only if using Yandex Music source
```

## Usage

```bash
# List currently available sources
music-loader sources

# Download from a CSV
music-loader download examples/tracks.csv -o ./library

# Download a Spotify playlist (needs spotdl + Spotify creds)
music-loader download "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

# Custom source order
music-loader download tracks.csv --sources yandex bandcamp youtube

# Parallel downloads
music-loader download tracks.csv -p 4

# Download + upload to configured cloud in one go
music-loader cloud-setup   # one-time, see above
music-loader download tracks.csv --upload
```

## Input formats

### CSV
```csv
artist,title,album,year
Pink Floyd,Time,The Dark Side of the Moon,1973
```

### JSON
```json
[{"artist": "Pink Floyd", "title": "Time", "album": "DSOTM", "year": 1973}]
```

### Plain text
```
Pink Floyd - Time
Led Zeppelin - Stairway to Heaven
```

### Spotify URL
```
https://open.spotify.com/playlist/...
```

## Output layout

```
library/
├── Pink Floyd/
│   ├── The Dark Side of the Moon/
│   │   ├── Time.mp3
│   │   ├── Money.mp3
│   │   └── ...
│   └── The Wall/
│       └── ...
└── .loader.log.jsonl        # per-track status log
```

## How the source chain works

For each track, sources are tried in order:

1. **Enrichment** (optional, `--no-enrich` to skip): iTunes (primary, gives
   duration + cover) and MusicBrainz (fallback, no duration) fill in canonical
   `artist`, `title`, `album`, `year`, `track_no`, `duration`, `cover_url`
2. `search(artist, title, album)` returns the best-scoring result (or `None`)
3. If score is below `--min-score`, skip to the next source
4. `download(info, path)` writes the MP3
5. **Duration sanity check**: if we have an expected duration (from iTunes) and
   the downloaded file is >2x or <0.4x that length, reject it (likely a full
   album, short clip, or live medley instead of the single track)
6. If `download` raises or returns `False`, retry up to `--retries` times, then move on
7. If every source fails, the track is logged to `.loader.log.jsonl` with `status=failed`

## Uploading to cloud storage

**Recommended path** — works in the Russian `whitelist` of mobile-restricted
services and doesn't need app approvals or OAuth dances.

### Yandex.Disk (recommended)

```bash
music-loader cloud-setup
# choose [1] Yandex.Disk
```

You'll need a Yandex account and a one-time app password:

1. Go to https://id.yandex.ru/security/app-passwords
2. Click "Создать новый пароль" (Create new password)
3. Name it `music-loader`, scope: `WebDAV`
4. Copy the generated password (you won't see it again)
5. In the wizard, enter your Yandex login (e.g. `user@yandex.ru`) and the app password

The loader uploads your library to `/music/Artist/Album/Track.mp3` on
Yandex.Disk. From your phone, open the **Yandex.Disk** app (in the whitelist)
→ `/music/` → play. No setup on the phone side.

```bash
# Other commands
music-loader cloud-status    # check connection, see what's saved
music-loader cloud-test file.mp3   # upload one file to verify
music-loader cloud-logout    # remove saved credentials
```

### Cloud.Mail.ru (alternative)

Same flow, pick option `[2]` instead. WebDAV endpoint: `webdav.cloud.mail.ru`.
8GB free, official **Cloud.Mail.ru** app on phone.

## Quick start (typical first run)

```bash
# 1. one-time: tell music-loader where to upload
music-loader cloud-setup

# 2. one-time: check which sources are reachable from your network
music-loader doctor

# 3. download (auto-detects sources, parallel = 4)
music-loader download tracks.csv -o ./library --parallel 4

# 4. upload to your Yandex.Disk
music-loader upload ./library
```

Subsequent runs:

```bash
# new tracks
music-loader download new_tracks.csv --parallel 4

# check token / connection
music-loader cloud-status
```

## Architecture

```
loader/
├── __main__.py            # entry point (`python -m loader`)
├── cli.py                 # argparse + subcommands
├── config.py              # env-driven config dataclass
├── loaders.py             # CSV/JSON/TXT/Spotify input
├── metadata.py            # ID3 + cover art via mutagen
├── pipeline.py            # orchestrates the source chain
├── webdav.py              # WebDAV client (Yandex.Disk / Cloud.Mail.ru)
├── storage.py             # Cloud storage backends
├── credentials.py         # secure credential storage (~/.config/music-loader/)
└── sources/
    ├── base.py            # Source ABC, TrackInfo dataclass
    ├── ytdlp_based.py     # YouTube, Bandcamp, SoundCloud (shared logic)
    ├── ytdlp_extras.py    # Bilibili, Dailymotion
    ├── yandex.py          # Yandex Music (yandex-music lib)
    ├── jamendo.py         # Jamendo (REST, free CC)
    ├── audius.py          # Audius (free streaming, decentralized)
    ├── archiveorg.py      # Internet Archive
    ├── openverse.py       # Openverse / CC Search
    ├── wikicommons.py     # Wikimedia Commons
    ├── itunes.py          # iTunes Search (enricher + 30s preview)
    └── registry.py        # default chain builder
```

All optional dependencies (yandex-music, spotdl) are imported lazily inside
source classes, so a stripped-down build with just yt-dlp + mutagen still
runs — unavailable sources are silently skipped.

## Building the binary

```bash
./build.sh
# -> dist/music-loader
```

The spec file uses `collect_all` for `yt_dlp` (which has dynamic extractor
plugins) and best-effort `collect_all` for the optional libraries. Excludes
heavy GUI/numeric stacks (tkinter, numpy, pandas, PyQt) to keep size sane.

## License

MIT, do whatever you want.
