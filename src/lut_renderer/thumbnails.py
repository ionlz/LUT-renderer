from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

APP_NAME = "lut-renderer"


def _thumb_dir() -> Path:
    from platformdirs import user_cache_dir

    path = Path(user_cache_dir(APP_NAME)) / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _thumb_key(source: Path) -> str:
    stat = source.stat()
    key = f"{source.resolve()}:{stat.st_mtime_ns}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def ensure_thumbnail(source: Path, width: int = 160) -> Optional[Path]:
    out = _thumb_dir() / f"{_thumb_key(source)}.jpg"
    if out.exists():
        return out

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        "0",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-1",
        "-q:v",
        "4",
        str(out),
    ]
    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return out if out.exists() else None
