#!/usr/bin/env python3
"""Device portal: Wi-Fi + credentials + figure registration (local HTTP)."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORTAL_DIR = Path(__file__).resolve().parent
DEVICE_ROOT = PORTAL_DIR.parent
STAGE_DIR = DEVICE_ROOT / "stage"
STATIC_DIR = PORTAL_DIR / "static"
sys.path.insert(0, str(DEVICE_ROOT))
sys.path.insert(0, str(PORTAL_DIR))
sys.path.insert(0, str(STAGE_DIR))

from config_store import (  # noqa: E402
    CAMERA_LOCK_PATH,
    apply_config_to_environ,
    ensure_device_identity,
    load_config,
    public_view,
    save_config,
)
FRESH_STAGE_FLAG = DEVICE_ROOT / ".fresh_stage_start"
from figure_store import (  # noqa: E402
    PERSONA_SAMPLES,
    delete_persona,
    merge_cloud_figures,
    save_persona,
)

HOST = os.environ.get("FS_PORTAL_HOST", "0.0.0.0")
PORT = int(os.environ.get("FS_PORTAL_PORT", "8080"))


def _read_static(name: str) -> bytes:
    path = STATIC_DIR / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "FigureStagePortal/2.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[portal] {self.address_string()} - {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def _html(self, name: str) -> None:
        try:
            self._send(200, _read_static(name), "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send(404, b"not found", "text/plain")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._html("index.html")
        if path == "/wifi":
            return self._html("wifi.html")
        if path == "/credentials":
            return self._html("credentials.html")
        if path == "/figures":
            return self._html("figures.html")
        if path == "/api/config":
            cfg = ensure_device_identity(load_config())
            return self._json(200, {"ok": True, "config": public_view(cfg)})
        if path == "/api/wifi/scan":
            try:
                from wifi_manager import scan_wifi

                nets = scan_wifi()
                return self._json(
                    200,
                    {
                        "ok": True,
                        "networks": [
                            {"ssid": n.ssid, "signal": n.signal, "security": n.security} for n in nets
                        ],
                    },
                )
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/figures":
            apply_config_to_environ()
            try:
                from cloud_client import list_figures

                figs = merge_cloud_figures(list_figures())
                return self._json(200, {"ok": True, "figures": figs})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        if path == "/api/persona-samples":
            return self._json(200, {"ok": True, "samples": PERSONA_SAMPLES})
        if path == "/api/status":
            try:
                from supervisor.readiness import device_state

                return self._json(200, {"ok": True, **device_state()})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/config":
            try:
                data = self._read_json()
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
            allowed = {
                "DOUBAO_APP_ID",
                "DOUBAO_ACCESS_KEY",
                "DOUBAO_APP_KEY",
                "DOUBAO_RESOURCE_ID",
                "DOUBAO_WS_URL",
                "CLOUD_BASE_URL",
                "DEVICE_CLOUD_TOKEN",
                "LUCKIN_ENABLED",
                "LUCKIN_TOKEN",
                "LUCKIN_LONGITUDE",
                "LUCKIN_LATITUDE",
                "AUDIO_DEVICE_ID",
                "DOUBAO_ENABLE_WEBSEARCH",
                "DOUBAO_WEBSEARCH_API_KEY",
                "DOUBAO_WEBSEARCH_BOT_ID",
            }
            updates = {k: str(data[k]) for k in allowed if k in data}
            cfg = save_config(updates)
            return self._json(200, {"ok": True, "config": public_view(cfg)})

        if path == "/api/wifi/connect":
            try:
                data = self._read_json()
                from wifi_manager import connect_wifi

                ssid = str(data.get("ssid") or "")
                connect_wifi(ssid, str(data.get("password") or ""))
                from wifi_manager import portal_urls

                urls = portal_urls()
                home = urls["home_url"]
                return self._json(
                    200,
                    {
                        "ok": True,
                        "message": "设备已连上 Wi‑Fi",
                        "ssid": ssid,
                        "portal": urls,
                        "next_step": {
                            "title": "请改用手机连同一 Wi‑Fi",
                            "detail": f"热点即将关闭。请收藏此地址，后续凭证与手办都在同一门户完成：",
                            "home_url": home,
                            "lan_ip_url": urls.get("lan_ip_url"),
                        },
                    },
                )
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})

        if path == "/api/figures/register":
            return self._register_figure()

        if path.startswith("/api/figures/") and path.endswith("/delete"):
            fid = path[len("/api/figures/") : -len("/delete")]
            apply_config_to_environ()
            try:
                from cloud_client import delete_figure

                delete_figure(fid)
                delete_persona(fid)
                return self._json(200, {"ok": True})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)})

        if path.startswith("/api/figures/") and path.endswith("/persona"):
            fid = path[len("/api/figures/") : -len("/persona")]
            try:
                data = self._read_json()
                persona = str(data.get("persona") or "").strip()
                if not persona:
                    return self._json(400, {"ok": False, "error": "人设不能为空"})
                save_persona(fid, persona)
                return self._json(200, {"ok": True})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})

        self._json(404, {"ok": False, "error": "not found"})

    def _register_figure(self) -> None:
        apply_config_to_environ()
        try:
            data = self._read_json()
            name = str(data.get("name") or "手办").strip()
            voice = str(data.get("voice_preset") or "wdog").strip()
            persona = str(data.get("persona") or "").strip()
            if not persona:
                return self._json(400, {"ok": False, "error": "请填写手办人设（仅存本机，不上传云端）"})
            # Only device CSI/OpenCV capture — same viewpoint as later recognize
            jpegs = self._capture_locked()
        except Exception as e:
            return self._json(400, {"ok": False, "error": str(e)})

        if not jpegs:
            return self._json(400, {"ok": False, "error": "采帧失败：请确认摄像头可用，并先停止 run_stage"})

        try:
            from cloud_client import create_figure

            result = create_figure(name, voice, jpegs)
            fid = str(result.get("figure_id") or "")
            if fid and persona:
                save_persona(fid, persona, name=name, voice_preset=voice)
            FRESH_STAGE_FLAG.write_text(str(fid or ""), encoding="utf-8")
            return self._json(200, {"ok": True, **result})
        except Exception as e:
            return self._json(500, {"ok": False, "error": str(e)})

    def _capture_locked(self) -> list[bytes]:
        clear_camera = None
        try:
            from supervisor.control import clear_camera_request, request_camera_for_portal

            clear_camera = clear_camera_request
            request_camera_for_portal()
        except ImportError:
            if CAMERA_LOCK_PATH.exists():
                raise RuntimeError("摄像头正被舞台程序占用，请先停止 run_stage")

        CAMERA_LOCK_PATH.write_text("portal", encoding="utf-8")
        try:
            from camera_capture import capture_frames

            return capture_frames(8)
        finally:
            try:
                CAMERA_LOCK_PATH.unlink(missing_ok=True)
            except TypeError:
                if CAMERA_LOCK_PATH.exists():
                    CAMERA_LOCK_PATH.unlink()
            if clear_camera is not None:
                clear_camera()


def serve_portal() -> None:
    ensure_device_identity()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[portal] http://{HOST}:{PORT}/")
    httpd.serve_forever()


def main() -> None:
    serve_portal()


if __name__ == "__main__":
    main()
