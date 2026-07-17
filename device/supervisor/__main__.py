#!/usr/bin/env python3
"""Figure Stage device supervisor — portal, hotspot, readiness, stage process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

DEVICE_ROOT = Path(__file__).resolve().parent.parent
PORTAL_DIR = DEVICE_ROOT / "portal"
STAGE_DIR = DEVICE_ROOT / "stage"
sys.path.insert(0, str(PORTAL_DIR))
sys.path.insert(0, str(STAGE_DIR))

from config_store import apply_config_to_environ, ensure_device_identity  # noqa: E402
from supervisor.control import (  # noqa: E402
    camera_requested,
    stage_process_running,
    write_stage_pid,
)
from supervisor.prompt_player import play_prompt  # noqa: E402
from supervisor.readiness import device_state  # noqa: E402
from wifi_manager import (  # noqa: E402
    HOTSPOT_PASSWORD,
    HOTSPOT_SSID,
    WifiError,
    ensure_hotspot,
    hotspot_gateway_ip,
    stop_hotspot,
    wait_for_wifi,
)

WAIT_SEC = float(os.environ.get("FS_WIFI_WAIT_SEC", "20"))
POLL_SEC = float(os.environ.get("FS_SUPERVISOR_POLL_SEC", "2"))
AUTO_STAGE = os.environ.get("FS_AUTO_STAGE", "true").strip().lower() in ("1", "true", "yes", "on")

_running = True
_stage_proc: subprocess.Popen | None = None
_portal_thread: threading.Thread | None = None
_prompted_phases: set[str] = set()


def _stop_stage(timeout: float = 8.0) -> None:
    global _stage_proc
    proc = _stage_proc
    _stage_proc = None
    write_stage_pid(None)
    if proc is None or proc.poll() is not None:
        # 清残留锁，便于门户立刻采帧
        try:
            (DEVICE_ROOT / ".camera.lock").unlink(missing_ok=True)
        except OSError:
            pass
        return
    print("[supervisor] 停止舞台进程…")
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    try:
        (DEVICE_ROOT / ".camera.lock").unlink(missing_ok=True)
    except OSError:
        pass
    time.sleep(0.8)


def _start_stage() -> None:
    global _stage_proc
    if _stage_proc is not None and _stage_proc.poll() is None:
        return
    apply_config_to_environ()
    env = os.environ.copy()
    cmd = [sys.executable, str(STAGE_DIR / "run_stage.py")]
    print("[supervisor] 启动舞台:", " ".join(cmd))
    _stage_proc = subprocess.Popen(cmd, cwd=str(DEVICE_ROOT), env=env)
    write_stage_pid(_stage_proc.pid)


def _start_portal_thread() -> None:
    global _portal_thread

    def _run() -> None:
        import server as portal_server

        portal_server.serve_portal()

    if _portal_thread and _portal_thread.is_alive():
        return
    _portal_thread = threading.Thread(target=_run, name="portal", daemon=True)
    _portal_thread.start()
    time.sleep(0.5)
    port = os.environ.get("FS_PORTAL_PORT", "8080")
    print(f"[supervisor] 门户线程已启动 http://0.0.0.0:{port}/")


def _handle_hotspot(wifi: bool) -> None:
    if wifi:
        try:
            stop_hotspot()
        except WifiError as e:
            print(f"[supervisor] stop_hotspot: {e}")
        return
    try:
        ensure_hotspot()
        gw = hotspot_gateway_ip()
        print(
            f"[supervisor] 热点 SSID={HOTSPOT_SSID!r} 密码={HOTSPOT_PASSWORD!r} "
            f"→ http://{gw}:{os.environ.get('FS_PORTAL_PORT', '8080')}/"
        )
    except WifiError as e:
        print(f"[supervisor] 无法开热点: {e}")


def _maybe_prompt(phase: str) -> None:
    if phase in _prompted_phases:
        return
    played = False
    if phase == "offline":
        played = play_prompt("network_offline")
    elif phase == "need_figures":
        played = play_prompt("network_connected_register")
    elif phase == "stage_ready":
        played = play_prompt("stage_ready")
    if played:
        _prompted_phases.add(phase)


def _tick() -> None:
    global _prompted_phases

    if camera_requested():
        _stop_stage()
        return

    st = device_state()
    phase = st["phase"]

    if not st["wifi"]:
        _handle_hotspot(False)
        _stop_stage()
        _maybe_prompt("offline")
    else:
        _handle_hotspot(True)
        if phase != "offline":
            _prompted_phases.discard("offline")
        if phase == "need_figures" and st["credentials_ok"]:
            _maybe_prompt("need_figures")
        elif phase == "stage_ready":
            _maybe_prompt("stage_ready")
            _prompted_phases.discard("need_figures")

    if not AUTO_STAGE:
        return

    if st["stage_ready"] and not camera_requested():
        if _stage_proc is None or _stage_proc.poll() is not None:
            if not stage_process_running():
                _start_stage()
    elif phase in ("offline", "need_credentials") or camera_requested():
        _stop_stage()
    elif phase == "need_figures" and _stage_proc is not None:
        _stop_stage()


def _shutdown(*_args) -> None:
    global _running
    print("[supervisor] 退出…")
    _running = False
    _stop_stage()


def main() -> int:
    ensure_device_identity()
    apply_config_to_environ()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print("[supervisor] Figure Stage 设备监督进程")
    print(f"[supervisor] 等待 Wi‑Fi（最多 {WAIT_SEC:.0f}s）…")
    if wait_for_wifi(WAIT_SEC):
        print("[supervisor] Wi‑Fi 已连接")
    else:
        print("[supervisor] 无 Wi‑Fi → 配网/热点模式")

    _start_portal_thread()

    while _running:
        try:
            _tick()
        except Exception as e:
            print(f"[supervisor] tick 异常: {e}")
        time.sleep(POLL_SEC)

    _stop_stage()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
