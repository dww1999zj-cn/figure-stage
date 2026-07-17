"""Camera capture for registration — Picamera2 first (CSI); OpenCV only as last resort."""

from __future__ import annotations

import time


def capture_frames(count: int = 8, interval: float = 0.15) -> list[bytes]:
    """Return list of JPEG bytes. Raises RuntimeError if no camera."""
    frames_rgb = _capture_rgb(count, interval)
    import cv2

    out: list[bytes] = []
    for rgb in frames_rgb:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if ok:
            out.append(buf.tobytes())
    if not out:
        raise RuntimeError("采帧失败")
    return out


def _capture_rgb(count: int, interval: float) -> list:
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            return _picamera2_capture(count, interval)
        except Exception as e:
            last_err = e
            print(f"[camera] Picamera2 第 {attempt} 次失败: {e}")
            time.sleep(1.0 * attempt)

    # CSI (imx219) 通常不能靠 OpenCV /dev/video0 稳定采帧；仅作最后尝试并带上原错误
    try:
        return _opencv_capture(count, interval)
    except Exception as e:
        raise RuntimeError(
            f"摄像头采帧失败（多为舞台未释放镜头）。Picamera2: {last_err}；OpenCV: {e}"
        ) from e


def _picamera2_capture(count: int, interval: float) -> list:
    from picamera2 import Picamera2
    import numpy as np

    picam2 = Picamera2()
    try:
        cfg = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 30},
        )
        picam2.configure(cfg)
        picam2.start()
        time.sleep(0.6)
        frames = []
        for _ in range(count):
            frame = picam2.capture_array()
            if frame.shape[2] == 4:
                frame = frame[..., :3]
            frames.append(np.ascontiguousarray(frame))
            time.sleep(interval)
        if len(frames) < max(2, count // 3):
            raise RuntimeError("Picamera2 采帧过少")
        return frames
    finally:
        try:
            picam2.stop()
        except Exception:
            pass
        try:
            picam2.close()
        except Exception:
            pass


def _opencv_capture(count: int, interval: float) -> list:
    import cv2

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("无法打开 OpenCV 摄像头")
    frames = []
    try:
        for _ in range(count):
            ok, bgr = cap.read()
            if not ok:
                continue
            frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            time.sleep(interval)
    finally:
        cap.release()
    if len(frames) < max(2, count // 3):
        raise RuntimeError("OpenCV 采帧过少（CSI 摄像头请用 Picamera2，勿依赖 OpenCV）")
    return frames
