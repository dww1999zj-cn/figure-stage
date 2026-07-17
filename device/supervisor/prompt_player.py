"""Play local WAV prompts (user-provided files under device/prompts/)."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

DEVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPTS_DIR = DEVICE_ROOT / "prompts"

_lock = threading.Lock()
_last_played: dict[str, float] = {}
_COOLDOWN_SEC = float(os.environ.get("FS_PROMPT_COOLDOWN_SEC", "45"))


def prompts_dir() -> Path:
    return Path(os.environ.get("FS_PROMPTS_DIR", str(DEFAULT_PROMPTS_DIR)))


def prompt_path(name: str) -> Path | None:
    base = prompts_dir()
    for ext in (".wav", ".WAV", ".mp3", ".MP3"):
        p = base / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def _play_file(path: Path) -> None:
    if shutil.which("aplay"):
        subprocess.run(
            ["aplay", "-q", str(path)],
            check=False,
            timeout=120,
        )
        return
    if shutil.which("paplay"):
        subprocess.run(
            ["paplay", str(path)],
            check=False,
            timeout=120,
        )
        return
    print(f"[prompt] 未找到 aplay/paplay，跳过播放: {path}")


def play_prompt(name: str, *, force: bool = False) -> bool:
    """Play prompts/<name>.wav if present. Returns True if played."""
    path = prompt_path(name)
    if path is None:
        print(f"[prompt] 无音频文件: {prompts_dir()}/{name}.wav（可自备）")
        return False

    import time

    now = time.time()
    with _lock:
        if not force and now - _last_played.get(name, 0) < _COOLDOWN_SEC:
            return False
        _last_played[name] = now

    print(f"[prompt] 播放: {path.name}")
    try:
        _play_file(path)
    except Exception as e:
        print(f"[prompt] 播放失败 {path}: {e}")
        return False
    return True
