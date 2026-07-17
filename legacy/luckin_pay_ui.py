"""瑞幸支付展示：本地二维码 + 可选全屏 + 局域网手机页。

树莓派无屏时，手机连同一 Wi‑Fi 打开 http://<pi-ip>:端口 即可扫码/点开支付。
"""

from __future__ import annotations

import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

_pay_lock = threading.Lock()
_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None
_current: dict[str, Any] = {
    "pay_url": "",
    "qr_path": "",
    "speak": "",
}


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def pay_output_dir() -> Path:
    raw = os.environ.get("LUCKIN_PAY_DIR", "").strip()
    if raw:
        path = Path(raw)
    else:
        path = Path(__file__).resolve().parent / "luckin_pay"
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def pay_port() -> int:
    return int(os.environ.get("LUCKIN_PAY_PORT", "8765"))


def pay_page_url() -> str:
    return f"http://{local_ip()}:{pay_port()}/"


def _download_bytes(url: str) -> bytes | None:
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return None
            return resp.content
    except Exception:
        return None


def _make_qr_png(content: str, out_path: Path) -> bool:
    try:
        import qrcode  # type: ignore
    except ImportError:
        return False
    img = qrcode.make(content)
    img.save(out_path)
    return out_path.is_file()


def resolve_pay_qr(
    *,
    pay_url: str | None,
    pay_qr_url: str | None,
) -> Path | None:
    """Prefer official QR image URL; else generate QR from pay_url."""
    out = pay_output_dir() / "latest_pay_qr.png"
    if pay_qr_url:
        data = _download_bytes(pay_qr_url)
        if data:
            out.write_bytes(data)
            return out
    if pay_url and _make_qr_png(pay_url, out):
        return out
    return out if out.is_file() else None


def _ensure_http_server() -> None:
    global _server, _server_thread
    if _server is not None:
        return
    if not _truthy("LUCKIN_PAY_HTTP", "true"):
        return

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            with _pay_lock:
                pay_url = _current.get("pay_url") or ""
                qr_path = _current.get("qr_path") or ""
                speak = _current.get("speak") or ""

            if self.path.startswith("/qr"):
                path = Path(qr_path) if qr_path else None
                if path and path.is_file():
                    data = path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(404, "no qr")
                return

            if self.path in ("/", "/index.html", "/pay"):
                safe_url = quote(pay_url, safe=":/?&=#%")
                has_qr = bool(qr_path and Path(qr_path).is_file())
                qr_block = (
                    '<img src="/qr" alt="pay qr" style="width:min(80vw,320px);height:auto;background:#fff;padding:12px;border-radius:8px"/>'
                    if has_qr
                    else "<p>暂无二维码图片，请点下方链接支付。</p>"
                )
                link_block = (
                    f'<p><a href="{safe_url}">打开支付链接</a></p>'
                    if pay_url
                    else "<p>暂无支付链接</p>"
                )
                html = f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>瑞幸支付</title>
<style>
body{{font-family:system-ui,sans-serif;background:#111;color:#f5f5f5;margin:0;padding:24px;text-align:center}}
a{{color:#7dd3fc}}
</style></head><body>
<h1>瑞幸待支付</h1>
<p>{speak}</p>
{qr_block}
{link_block}
<p style="opacity:.7;font-size:14px">同一 Wi‑Fi 下用手机打开本页，微信扫码或点链接完成支付。</p>
</body></html>"""
                data = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_error(404)

    port = pay_port()
    try:
        _server = HTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        print(f"[Luckin] 支付页端口 {port} 不可用: {e}")
        _server = None
        return

    def _run() -> None:
        assert _server is not None
        _server.serve_forever(poll_interval=0.5)

    _server_thread = threading.Thread(target=_run, daemon=True, name="luckin-pay-http")
    _server_thread.start()
    print(f"[Luckin] 支付页已启动: {pay_page_url()}")


def _show_on_display(qr_path: Path) -> None:
    if os.environ.get("DISPLAY") is None:
        return
    if not _truthy("LUCKIN_PAY_SHOW_WINDOW", "true"):
        return
    try:
        import cv2
        import numpy as np
    except ImportError:
        return

    data = np.fromfile(str(qr_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return

    def _ui() -> None:
        win = "Luckin Pay QR"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 480, 480)
        cv2.imshow(win, img)
        cv2.waitKey(1)

    # OpenCV window must run briefly; stage loop also has GUI — best-effort
    threading.Thread(target=_ui, daemon=True).start()


def present_payment(
    *,
    pay_url: str | None,
    pay_qr_url: str | None = None,
    speak: str | None = None,
) -> str | None:
    """Prepare QR + HTTP page. Returns phone page URL if available."""
    if not pay_url and not pay_qr_url:
        return None

    qr_path = resolve_pay_qr(pay_url=pay_url, pay_qr_url=pay_qr_url)
    page = None
    with _pay_lock:
        _current["pay_url"] = pay_url or ""
        _current["qr_path"] = str(qr_path) if qr_path else ""
        _current["speak"] = speak or "请完成支付"
    _ensure_http_server()
    if _server is not None:
        page = pay_page_url()

    if qr_path:
        print(f"[Luckin] 支付二维码已保存: {qr_path}")
        _show_on_display(qr_path)
    if pay_url:
        print(f"[Luckin] 支付链接: {pay_url}")
    if page:
        print(f"[Luckin] 手机打开扫码: {page}")
    return page
