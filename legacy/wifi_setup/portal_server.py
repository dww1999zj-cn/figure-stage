"""Lightweight captive Wi-Fi setup portal (stdlib only)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from wifi_manager import WifiError, connect_wifi, scan_wifi

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"

# Shared connect result for UI polling
_state_lock = threading.Lock()
_connect_state: dict = {"busy": False, "ok": None, "message": ""}


def get_connect_state() -> dict:
    with _state_lock:
        return dict(_connect_state)


def _set_connect_state(**kwargs) -> None:
    with _state_lock:
        _connect_state.update(kwargs)


class PortalHandler(BaseHTTPRequestHandler):
    server_version = "FigureStageWifiPortal/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[portal] {self.address_string()} - {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            if not INDEX_HTML.is_file():
                self._send(500, b"index.html missing", "text/plain; charset=utf-8")
                return
            body = INDEX_HTML.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path == "/api/scan":
            try:
                nets = scan_wifi()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "networks": [
                            {"ssid": n.ssid, "signal": n.signal, "security": n.security}
                            for n in nets
                        ],
                    },
                )
            except WifiError as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/status":
            self._send_json(200, {"ok": True, **get_connect_state()})
            return
        # Captive portal probes — redirect to setup page
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/connect":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = (self.headers.get("Content-Type") or "").lower()

        ssid = ""
        password = ""
        try:
            if "application/json" in ctype:
                data = json.loads(raw.decode("utf-8") or "{}")
                ssid = str(data.get("ssid") or "")
                password = str(data.get("password") or "")
            else:
                form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                ssid = (form.get("ssid") or [""])[0]
                password = (form.get("password") or [""])[0]
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"ok": False, "error": f"无效请求: {e}"})
            return

        if get_connect_state().get("busy"):
            self._send_json(409, {"ok": False, "error": "正在连接中，请稍候"})
            return

        _set_connect_state(busy=True, ok=None, message="正在连接…")

        def worker() -> None:
            try:
                connect_wifi(ssid, password)
                _set_connect_state(busy=False, ok=True, message=f"已连接「{ssid.strip()}」")
            except WifiError as e:
                _set_connect_state(busy=False, ok=False, message=str(e))
            except Exception as e:  # noqa: BLE001
                _set_connect_state(busy=False, ok=False, message=f"连接异常: {e}")

        threading.Thread(target=worker, daemon=True).start()
        self._send_json(202, {"ok": True, "message": "已提交，正在连接 Wi‑Fi…"})


def create_server(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), PortalHandler)


def serve_forever(host: str = "0.0.0.0", port: int = 8080) -> None:
    httpd = create_server(host, port)
    print(f"[portal] listening on http://{host}:{port}/")
    httpd.serve_forever()
