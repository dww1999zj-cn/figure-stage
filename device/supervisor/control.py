"""Supervisor ↔ portal/stage IPC (files under device/.supervisor/)."""

from __future__ import annotations

import os
import time
from pathlib import Path

DEVICE_ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR_DIR = DEVICE_ROOT / ".supervisor"
CAMERA_LOCK_PATH = DEVICE_ROOT / ".camera.lock"
STAGE_PID_PATH = SUPERVISOR_DIR / "stage.pid"
CAMERA_REQUEST_PATH = SUPERVISOR_DIR / "camera_request"


def ensure_dirs() -> None:
    SUPERVISOR_DIR.mkdir(parents=True, exist_ok=True)


def write_stage_pid(pid: int | None) -> None:
    ensure_dirs()
    if pid is None:
        STAGE_PID_PATH.unlink(missing_ok=True)
    else:
        STAGE_PID_PATH.write_text(str(pid), encoding="utf-8")


def read_stage_pid() -> int | None:
    if not STAGE_PID_PATH.is_file():
        return None
    try:
        return int(STAGE_PID_PATH.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def stage_process_running() -> bool:
    pid = read_stage_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        STAGE_PID_PATH.unlink(missing_ok=True)
        return False


def request_camera_for_portal(timeout_sec: float = 25.0) -> None:
    """Ask supervisor to stop stage and wait until camera is free."""
    ensure_dirs()
    CAMERA_REQUEST_PATH.write_text("portal", encoding="utf-8")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not stage_process_running() and not CAMERA_LOCK_PATH.exists():
            # libcamera 释放管道需要片刻，避免立刻 Picamera2 失败再误用 OpenCV
            time.sleep(1.5)
            return
        time.sleep(0.25)
    # 舞台已死但锁文件残留时清掉再等一会
    if not stage_process_running() and CAMERA_LOCK_PATH.exists():
        try:
            CAMERA_LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        time.sleep(1.5)
        return
    raise RuntimeError("摄像头仍被占用：请稍候再试，或执行 sudo systemctl restart figure-stage")


def clear_camera_request() -> None:
    try:
        CAMERA_REQUEST_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def camera_requested() -> bool:
    return CAMERA_REQUEST_PATH.is_file()
