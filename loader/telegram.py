"""Telegram Bot API client for music library sync.

Sends audio files to a private Telegram channel via a bot
(created with @BotFather). Uses only `requests` — no extra deps,
same style as webdav.py.

Limits (Bot API):
  - max 50 MB per file (library tracks are ~1-17 MB .opus — fine)
  - ~30 msg/sec; we send sequentially with light throttling
  - audio caption max 1024 chars

Dedup: Telegram has no directory listing like WebDAV, so the client
keeps a local registry (JSON): remote caption path -> Telegram file_id.
Re-upload of an unchanged file (same size) is skipped. The registry
lives next to the credentials file.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# Bot API file size limit (50 MB). Tracks above this are skipped with a warning.
MAX_FILE_SIZE = 50 * 1024 * 1024

# Seconds between sends to stay far under flood limits.
SEND_DELAY = 0.5

REGISTRY_FILENAME = "telegram_registry.json"


class TelegramError(Exception):
    pass


def registry_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "music-loader" / REGISTRY_FILENAME


def _load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"failed to load telegram registry: {e}")
        return {}


def _save_registry(data: dict) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if sys.platform != "win32":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


class TelegramClient:
    """Minimal Bot API client: sendAudio + getMe for connection checks.

    Auth: bot token in the URL (standard Bot API scheme).
    Target: private channel chat_id (e.g. -1001234567890). The bot must
    be an admin of the channel.
    """

    def __init__(self, token: str, chat_id: str, timeout: int = 300):
        self.token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.timeout = timeout
        if not self.token:
            raise TelegramError("bot token is empty")
        if not self.chat_id:
            raise TelegramError("chat_id is empty")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "music-loader/0.1"
        self._registry: Optional[dict] = None

    def _url(self, method: str) -> str:
        return f"{TELEGRAM_API}/bot{self.token}/{method}"

    def _call(self, method: str, **kw) -> dict:
        try:
            r = self.session.post(self._url(method), timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise TelegramError(f"{method}: {e}") from e
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code} (not JSON)")
        if not data.get("ok"):
            desc = data.get("description", f"HTTP {r.status_code}")
            code = data.get("error_code", r.status_code)
            raise TelegramError(f"{method}: [{code}] {desc}")
        return data["result"]

    # ---- connection check ----
    def me(self) -> dict:
        """getMe — validates the token, returns bot info."""
        return self._call("getMe")

    def check_access(self) -> dict:
        """Validate token (getMe) and channel access (getChat)."""
        info = self.me()
        try:
            self._call("getChat", data={"chat_id": self.chat_id})
        except TelegramError as e:
            raise TelegramError(
                f"token OK (@{info.get('username', '?')}), "
                f"but channel unreachable: {e}. "
                "Add the bot as admin of the channel."
            )
        return info

    # ---- registry (caption path -> {file_id, size}) ----
    @property
    def registry(self) -> dict:
        if self._registry is None:
            self._registry = _load_registry()
        return self._registry

    def _registry_save(self) -> None:
        _save_registry(self.registry)

    def uploaded_size(self, caption_path: str) -> Optional[int]:
        entry = self.registry.get(caption_path)
        if isinstance(entry, dict):
            return entry.get("size")
        return None

    # ---- upload ----
    def upload(
        self,
        local_path: Path,
        caption_path: str,
        skip_existing: bool = True,
    ) -> bool:
        """Send one audio file to the channel. Returns True on success.

        caption_path is the library-relative identity, e.g.
        "Pink Floyd/Dark Side/Time" — used for the message caption and
        for the dedup registry.
        """
        local_path = Path(local_path)
        if not local_path.exists() or local_path.stat().st_size == 0:
            return False
        size = local_path.stat().st_size
        if size > MAX_FILE_SIZE:
            log.warning("  skip %s: %.1f MB > 50 MB Bot API limit",
                        local_path.name, size / (1024 * 1024))
            return False
        if skip_existing:
            known = self.uploaded_size(caption_path)
            if known is not None and known == size:
                return True
        # Caption: short identity, Telegram audio shows performer/title too.
        performer, _, title = caption_path.rpartition("/")
        if not title:
            title = local_path.stem
            performer = ""
        caption = f"{performer} — {title}"[:900] if performer else title[:900]
        # Duration + real filename: Telegram builds a proper audio player
        # (seek bar, track switching) from these. Without duration the
        # client renders the message as a voice note bubble.
        import mutagen
        duration = 0
        try:
            audio_meta = mutagen.File(str(local_path))
            if audio_meta is not None and getattr(audio_meta, "info", None):
                duration = int(getattr(audio_meta.info, "length", 0) or 0)
        except Exception:
            duration = 0
        with open(local_path, "rb") as f:
            result = self._call(
                "sendAudio",
                data={
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "performer": performer[:200],
                    "title": title[:200],
                    "duration": duration,
                },
                files={"audio": (local_path.name, f, "audio/ogg")},
            )
        audio = result.get("audio") or {}
        self.registry[caption_path] = {
            "file_id": audio.get("file_id", ""),
            "size": size,
        }
        self._registry_save()
        time.sleep(SEND_DELAY)
        return True

    def forget(self, caption_path: str) -> None:
        if caption_path in self.registry:
            del self.registry[caption_path]
            self._registry_save()
