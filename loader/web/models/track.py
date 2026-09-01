"""Track model: CRUD, search, metadata edits, library scanning."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..dbconn import DB_DIR, tx

log = logging.getLogger(__name__)


def add(
    session_id: int, artist: str, title: str, album: str = "",
    file_path: str = "", file_size: int = 0, duration: float = 0,
    status: str = "pending", source_name: str = "",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO tracks (session_id, artist, title, album, file_path, "
            "file_size, duration, status, source_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, artist, title, album, file_path,
             file_size, duration, status, source_name, now),
        )
        return cur.lastrowid


def update_status(track_id: int, status: str, file_path: str = "",
                   file_size: int = 0, source_name: str = "",
                   duration: float = 0) -> None:
    # The pipeline hands us absolute paths; the DB convention is relative
    # to the library root. Normalize here so every caller stays consistent.
    if file_path:
        try:
            file_path = str(Path(file_path).relative_to(DB_DIR))
        except ValueError:
            pass
    with tx() as conn:
        updates = ["status=?"]
        params: list = [status]
        if file_path:
            updates.append("file_path=?")
            params.append(file_path)
        if file_size:
            updates.append("file_size=?")
            params.append(file_size)
        if source_name:
            updates.append("source_name=?")
            params.append(source_name)
        if duration:
            updates.append("duration=?")
            params.append(float(duration))
        params.append(track_id)
        conn.execute(
            f"UPDATE tracks SET {', '.join(updates)} WHERE id=?", params
        )


def search(
    query: str = "",
    artist: str = "",
    album: str = "",
    status: str = "",
    limit: int = 100,
    offset: int = 0,
    sort: str = "artist",
    order: str = "asc",
    only_downloaded: bool = False,
    dedup: bool = False,
) -> tuple[list[dict], int]:
    where = []
    params: list = []
    if query:
        where.append("(artist LIKE ? OR title LIKE ? OR album LIKE ?)")
        q = f"%{query}%"
        params.extend([q, q, q])
    if artist:
        where.append("artist LIKE ?")
        params.append(f"%{artist}%")
    if album:
        where.append("album LIKE ?")
        params.append(f"%{album}%")
    if status:
        where.append("status=?")
        params.append(status)
    if only_downloaded:
        where.append("file_path != ''")
    if dedup:
        # Library listing must show each track exactly once, no matter
        # what state the DB is in (old runs, interrupted sessions, races).
        where.append(
            "id IN (SELECT MAX(id) FROM tracks WHERE file_path != '' "
            "GROUP BY LOWER(TRIM(artist)), LOWER(TRIM(title)))"
        )

    clause = " AND ".join(where) if where else "1"
    # Whitelisted sort columns (no user-controlled SQL)
    columns = {
        "artist": "artist", "title": "title", "album": "album",
        "created": "created_at", "duration": "duration",
        "size": "file_size",
    }
    order_col = columns.get(sort, "artist")
    direction = "DESC" if order.lower() == "desc" else "ASC"

    with tx() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM tracks WHERE {clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE {clause} ORDER BY {order_col} {direction} "
            "LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total


def get(track_id: int) -> Optional[dict]:
    with tx() as conn:
        row = conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        return dict(row) if row else None


def delete(track_id: int) -> bool:
    """Delete one track record and its file. Returns True if existed."""
    return delete_many([track_id]) > 0


def delete_many(track_ids: list[int]) -> int:
    """Delete multiple tracks and their files. Returns number removed."""
    removed = 0
    lib = DB_DIR.resolve()
    for tid in track_ids:
        track = get(tid)
        if not track:
            continue
        file_path = (lib / track["file_path"]).resolve()
        # Never delete files outside the library (bad paths in DB).
        if not file_path.is_relative_to(lib):
            log.warning("refusing to delete outside library: %s", file_path)
            file_path = None
        try:
            if file_path is not None and file_path.exists():
                file_path.unlink()
                log.info("deleted file: %s", file_path)
        except OSError as e:
            log.warning("could not delete file %s: %s", file_path, e)
        with tx() as conn:
            conn.execute("DELETE FROM tracks WHERE id=?", (tid,))
        removed += 1
    _cleanup_empty_dirs()
    return removed


def update_metadata(track_id: int, artist: str, title: str, album: str) -> tuple[bool, str]:
    """Edit track metadata: rename file and move to new Artist/Album dir."""
    track = get(track_id)
    if not track:
        return False, "track not found"
    if not title.strip():
        return False, "title required"

    artist = (artist or "Unknown Artist").strip()
    title = title.strip()
    album = (album or "Singles").strip()

    from ..pipeline import sanitize
    safe_artist = sanitize(artist) or "Unknown Artist"
    safe_album = sanitize(album) or "Singles"
    safe_title = sanitize(title, max_len=100)

    lib = DB_DIR.resolve()
    src = (lib / track["file_path"]).resolve()
    if not src.is_relative_to(lib):
        return False, "invalid file path"
    if not src.exists():
        return False, "file missing on disk"

    # Build new path
    if safe_artist and safe_artist != "Unknown Artist":
        filename = f"{safe_artist} - {safe_title}{src.suffix}"
    else:
        filename = f"{safe_title}{src.suffix}"
    dst_dir = DB_DIR / safe_artist / safe_album
    dst = dst_dir / filename

    if src.resolve() == dst.resolve():
        # Same path — just update DB record
        with tx() as conn:
            conn.execute(
                "UPDATE tracks SET artist=?, title=?, album=? WHERE id=?",
                (artist, title, album, track_id),
            )
        return True, "updated"

    # Ensure target dir exists, move file
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        src.rename(dst)
    except OSError as e:
        return False, f"move failed: {e}"

    rel = str(dst.relative_to(DB_DIR))
    with tx() as conn:
        conn.execute(
            "UPDATE tracks SET artist=?, title=?, album=?, file_path=? "
            "WHERE id=?",
            (artist, title, album, rel, track_id),
        )
    _cleanup_empty_dirs()
    return True, "renamed"


def duplicates() -> list[dict]:
    """Find tracks with the same artist+title (case-insensitive)."""
    with tx() as conn:
        rows = conn.execute(
            "SELECT LOWER(artist) la, LOWER(title) lt, artist, title, "
            "COUNT(*) cnt FROM tracks "
            "WHERE file_path != '' GROUP BY LOWER(artist), LOWER(title) "
            "HAVING cnt > 1 ORDER BY cnt DESC, artist LIMIT 200"
        ).fetchall()
    dups = []
    for r in rows:
        with tx() as conn:
            ids = conn.execute(
                "SELECT id, file_path, file_size, album, created_at FROM tracks "
                "WHERE LOWER(artist)=? AND LOWER(title)=? AND file_path != '' "
                "ORDER BY file_size DESC",
                (r["la"], r["lt"]),
            ).fetchall()
        dups.append({
            "artist": r["artist"],
            "title": r["title"],
            "count": r["cnt"],
            "tracks": [dict(i) for i in ids],
        })
    return dups


def scan_library(library_dir: Path) -> int:
    """Walk existing library on disk and index files not yet in DB."""
    from .session import create as create_session

    pending: list = []
    for f in sorted(library_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in (".opus", ".mp3"):
            continue
        rel = str(f.relative_to(library_dir))

        with tx() as conn:
            existing = conn.execute(
                "SELECT id FROM tracks WHERE file_path=?", (rel,)
            ).fetchone()
        if existing:
            continue

        parts = rel.split("/")
        artist = parts[0] if len(parts) >= 1 else ""
        album = parts[1] if len(parts) >= 2 else ""
        stem = f.stem
        title = stem
        if " - " in stem:
            _, title = stem.split(" - ", 1)
        pending.append((f, rel, artist, title, album))

    if not pending:
        return 0

    session_id = create_session(
        source="scan",
        source_name="Library scan (pre-existing files)",
        total=len(pending),
    )
    for f, rel, artist, title, album in pending:
        add(
            session_id=session_id,
            artist=artist,
            title=title,
            album=album,
            file_path=rel,
            file_size=f.stat().st_size,
            duration=_probe_duration(f),
            status="ok",
            source_name="",
        )
    with tx() as conn:
        conn.execute(
            "UPDATE import_sessions SET downloaded=? WHERE id=?", (len(pending), session_id)
        )
    return len(pending)


def _probe_duration(path: Path) -> float:
    """Get audio duration in seconds via ffprobe (best-effort)."""
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    return 0.0


def _cleanup_empty_dirs() -> None:
    """Remove empty Artist/Album dirs left after deletions."""
    lib = DB_DIR
    if not lib.exists():
        return
    for root, dirs, files in os.walk(str(lib), topdown=False):
        p = Path(root)
        if p == lib:
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
        except (PermissionError, OSError):
            pass
