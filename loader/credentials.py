"""Secure credential storage for cloud storage credentials.

Stored at ~/.config/music-loader/credentials.json (chmod 600) on Linux/macOS,
or %APPDATA%/music-loader/credentials.json on Windows.

File contents are plain JSON but the file is created with restricted
permissions. The user is expected to keep their machine secure.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .storage import CloudConfig

log = logging.getLogger(__name__)

CREDENTIALS_FILENAME = "credentials.json"


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "music-loader"
    d.mkdir(parents=True, exist_ok=True)
    return d


def credentials_path() -> Path:
    return _config_dir() / CREDENTIALS_FILENAME


def _chmod_600(p: Path) -> None:
    """Best-effort: restrict to owner read/write."""
    if sys.platform == "win32":
        return
    try:
        os.chmod(p, 0o600)
    except Exception as e:
        log.debug(f"chmod failed: {e}")


def _read() -> dict:
    p = credentials_path()
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"failed to load credentials: {e}")
        return {}


def _write(data: dict) -> Path:
    p = credentials_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    _chmod_600(p)
    return p


# ---- Cloud storage (Yandex.Disk / Cloud.Mail.ru) ----

def load_cloud() -> Optional[CloudConfig]:
    data = _read()
    cloud = data.get("cloud") or {}
    if not cloud:
        return None
    try:
        return CloudConfig(**{k: v for k, v in cloud.items()
                              if k in CloudConfig.__dataclass_fields__})
    except Exception as e:
        log.warning(f"failed to parse cloud config: {e}")
        return None


def save_cloud(config: CloudConfig) -> Path:
    data = _read()
    data["cloud"] = asdict(config)
    return _write(data)


def clear_cloud() -> bool:
    data = _read()
    if "cloud" in data:
        del data["cloud"]
        if not data:
            try:
                credentials_path().unlink()
            except OSError:
                pass
        else:
            _write(data)
        return True
    return False


def cloud_path() -> Path:
    return credentials_path()


# ---- Yandex OAuth (separate from cloud password) ----
# Required for the yandex_rest backend. Stored as a plain field; the
# token lasts ~1 year, no refresh in implicit flow.

def load_yandex_token() -> Optional[str]:
    data = _read()
    return data.get("yandex_token") or None


def save_yandex_token(token: str) -> Path:
    data = _read()
    data["yandex_token"] = token
    return _write(data)


def clear_yandex_token() -> bool:
    data = _read()
    if "yandex_token" in data:
        del data["yandex_token"]
        if not data:
            try:
                credentials_path().unlink()
            except OSError:
                pass
        else:
            _write(data)
        return True
    return False
