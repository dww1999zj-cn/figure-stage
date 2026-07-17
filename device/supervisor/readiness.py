"""Device readiness checks for supervisor."""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEVICE_ROOT = Path(__file__).resolve().parent.parent
PORTAL_DIR = DEVICE_ROOT / "portal"
STAGE_DIR = DEVICE_ROOT / "stage"
sys.path.insert(0, str(PORTAL_DIR))
sys.path.insert(0, str(STAGE_DIR))

from config_store import apply_config_to_environ, load_config  # noqa: E402
from wifi_manager import is_wifi_connected, portal_urls  # noqa: E402

REQUIRED_DOUBAO = ("DOUBAO_APP_ID", "DOUBAO_ACCESS_KEY", "DOUBAO_APP_KEY")
REQUIRED_CLOUD = ("CLOUD_BASE_URL", "DEVICE_CLOUD_TOKEN", "DEVICE_ID")


def check_credentials(cfg: dict[str, str] | None = None) -> tuple[bool, list[str]]:
    cfg = cfg or load_config()
    missing = [k for k in (*REQUIRED_CLOUD, *REQUIRED_DOUBAO) if not str(cfg.get(k) or "").strip()]
    return (len(missing) == 0, missing)


def check_figures_registered() -> tuple[bool, int]:
    apply_config_to_environ()
    try:
        from cloud_client import list_figures

        n = len(list_figures())
        return n > 0, n
    except Exception:
        return False, 0


def device_state() -> dict:
    """Aggregate readiness for supervisor and /api/status."""
    cfg = load_config()
    wifi = is_wifi_connected()
    creds_ok, creds_missing = check_credentials(cfg)
    figures_ok, figure_count = (False, 0)
    if wifi and creds_ok:
        figures_ok, figure_count = check_figures_registered()

    stage_ready = wifi and creds_ok and figures_ok
    if not wifi:
        phase = "offline"
    elif not creds_ok:
        phase = "need_credentials"
    elif not figures_ok:
        phase = "need_figures"
    else:
        phase = "stage_ready"

    urls = portal_urls()
    next_step = _next_step(phase, urls)
    return {
        "wifi": wifi,
        "credentials_ok": creds_ok,
        "credentials_missing": creds_missing,
        "figures_ok": figures_ok,
        "figure_count": figure_count,
        "stage_ready": stage_ready,
        "phase": phase,
        "portal": urls,
        "next_step": next_step,
    }


def _next_step(phase: str, urls: dict) -> dict:
    home = urls.get("home_url") or ""
    if phase == "offline":
        return {
            "title": "让设备连上家里 Wi‑Fi",
            "detail": f"手机连热点「{urls.get('hotspot_ssid', 'FigureStage-Setup')}」后，在此门户完成配网。",
            "action_href": "/wifi",
            "action_label": "去配网",
        }
    if phase == "need_credentials":
        return {
            "title": "已联网，请填写凭证",
            "detail": f"若刚配完网，请把手机改连同一 Wi‑Fi，并收藏固定地址 {home}（热点地址将失效）。",
            "action_href": "/credentials",
            "action_label": "填凭证",
        }
    if phase == "need_figures":
        return {
            "title": "请注册至少一个手办",
            "detail": "凭证已齐。固定摄像头采帧，视觉特征上传云端，人设仅存本机。",
            "action_href": "/figures",
            "action_label": "注册手办",
        }
    return {
        "title": "设置完成",
        "detail": "设备就绪，监督进程将自动启动舞台识别与语音。",
        "action_href": "/figures",
        "action_label": "管理手办",
    }
