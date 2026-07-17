"""Load device/config.env into os.environ before stage starts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

STAGE_DIR = Path(__file__).resolve().parent
DEVICE_ROOT = STAGE_DIR.parent
PORTAL_DIR = DEVICE_ROOT / "portal"
sys.path.insert(0, str(PORTAL_DIR))

from config_store import apply_config_to_environ, ensure_device_identity, config_path  # noqa: E402


def bootstrap() -> dict[str, str]:
    cfg = ensure_device_identity()
    apply_config_to_environ()
    # Prefer device config over empty shell env for required checks
    path = config_path()
    print(f"[config] {path} (DEVICE_ID={cfg.get('DEVICE_ID', '')[:8]}…)")
    return cfg
