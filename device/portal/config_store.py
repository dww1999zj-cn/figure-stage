"""Local device config.env — Doubao/Luckin keys never leave the device."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

DEVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(os.environ.get("FS_CONFIG_PATH", str(DEVICE_ROOT / "config.env")))
CAMERA_LOCK_PATH = DEVICE_ROOT / ".camera.lock"

MASK_KEYS = frozenset(
    {
        "DOUBAO_ACCESS_KEY",
        "DOUBAO_APP_KEY",
        "LUCKIN_TOKEN",
        "DEVICE_CLOUD_TOKEN",
    }
)


def config_path() -> Path:
    return DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> dict[str, str]:
    p = path or config_path()
    data: dict[str, str] = {}
    if not p.is_file():
        return data
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def apply_config_to_environ(path: Path | None = None) -> dict[str, str]:
    cfg = load_config(path)
    for k, v in cfg.items():
        os.environ[k] = v
    return cfg


def ensure_device_identity(cfg: dict[str, str] | None = None) -> dict[str, str]:
    """Ensure DEVICE_ID exists; persist if created. Cloud token is user-supplied (operator CLOUD_API_TOKEN)."""
    cfg = dict(cfg or load_config())
    if not cfg.get("DEVICE_ID"):
        cfg["DEVICE_ID"] = str(uuid.uuid4())
        save_config(cfg)
    return cfg


def save_config(updates: dict[str, str], path: Path | None = None, *, merge: bool = True) -> dict[str, str]:
    p = path or config_path()
    current = load_config(p) if merge else {}
    for k, v in updates.items():
        if v is None:
            continue
        # Empty string on masked fields means "keep previous"
        if k in MASK_KEYS and v == "" and k in current:
            continue
        current[k] = str(v)
    current = ensure_device_identity(current)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Figure Stage device config — keep on device only", ""]
    for k in sorted(current.keys()):
        lines.append(f"{k}={current[k]}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return current


def public_view(cfg: dict[str, str] | None = None) -> dict[str, str]:
    cfg = dict(cfg or load_config())
    cfg = ensure_device_identity(cfg)
    out = {}
    for k, v in cfg.items():
        if k in MASK_KEYS:
            out[k] = "********" if v else ""
            out[f"{k}_set"] = "1" if v else "0"
        else:
            out[k] = v
    return out
