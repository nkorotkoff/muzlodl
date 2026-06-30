# Changelog

All notable changes to music-loader are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Yandex.Disk uploads: removed double URL-encoding of remote paths
  (`get_upload_url` was pre-`quote()`-ing before `requests` re-encoded it,
  producing `MASSIVE%2520ADDICTIVE` and 409 CONFLICT on any path with spaces
  or non-ASCII characters).
- Wrong-match downloads from YouTube: tightened `min_match_score` 0.6 → 0.75
  and added an artist-match cap (0.5 when `artist_score < 0.3`) so fan-uploads
  with the right title but a different uploader no longer slip through.

## [0.1.0] - 2026-06-30

### Added
- Multi-source fallback chain: Archive.org → Openverse → Wikimedia Commons →
  Yandex Music → Audius → Bandcamp → SoundCloud → YouTube → Bilibili →
  Dailymotion → Jamendo.
- iTunes Search and MusicBrainz metadata enrichers (album, year, cover,
  duration) run before the source chain.
- Bulk input: CSV, JSON, plain text, or Spotify playlist URL.
- ID3 tag embedding with cover art via mutagen.
- `Artist/Album/Track.mp3` library layout; re-runs skip already-downloaded
  files.
- Yandex.Disk and Cloud.Mail.ru uploads via WebDAV / REST API.
- Duration sanity check rejects full-album or short-clip downloads that
  diverge from the expected length (>2x or <0.4x).
- Single-binary distribution via PyInstaller (`./build.sh`).
- Lazy imports of optional deps (`yandex-music`, `spotdl`) so a stripped
  build still runs.
- `music-loader doctor` health probe for all sources.
- `music-loader cloud-setup` / `cloud-status` / `cloud-test` /
  `cloud-logout` for cloud credential lifecycle.

[Unreleased]: https://github.com/OWNER/music-loader/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/music-loader/releases/tag/v0.1.0
