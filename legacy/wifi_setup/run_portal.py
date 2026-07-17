#!/usr/bin/env python3
"""Boot entry: wait for Wi-Fi; if offline, start hotspot + setup portal."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

# Allow running from any cwd
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from portal_server import create_server, get_connect_state  # noqa: E402
from wifi_manager import (  # noqa: E402
    HOTSPOT_PASSWORD,
    HOTSPOT_SSID,
    WifiError,
    ensure_hotspot,
    hotspot_gateway_ip,
    is_wifi_connected,
    stop_hotspot,
    wait_for_wifi,
)

WAIT_SEC = float(os.environ.get("FS_WIFI_WAIT_SEC", "20"))
PORT = int(os.environ.get("FS_PORTAL_PORT", "8080"))
HOST = os.environ.get("FS_PORTAL_HOST", "0.0.0.0")


def main() -> int:
    print(f"[wifi-portal] waiting up to {WAIT_SEC:.0f}s for existing Wi‑Fi…")
    if wait_for_wifi(WAIT_SEC):
        print("[wifi-portal] Wi‑Fi already connected — exiting setup mode")
        return 0

    print(f"[wifi-portal] no Wi‑Fi — starting hotspot SSID={HOTSPOT_SSID!r}")
    try:
        ensure_hotspot()
    except WifiError as e:
        print(f"[wifi-portal] failed to start hotspot: {e}", file=sys.stderr)
        return 1

    time.sleep(1.5)
    gw = hotspot_gateway_ip()
    print(f"[wifi-portal] open http://{gw}:{PORT}/  (AP password: {HOTSPOT_PASSWORD})")

    httpd = create_server(HOST, PORT)
    exit_code = 1

    def _shutdown(*_args) -> None:
        print("[wifi-portal] shutting down…")
        httpd.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    def serve() -> None:
        httpd.serve_forever(poll_interval=0.5)

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    try:
        while t.is_alive():
            st = get_connect_state()
            if st.get("ok") is True and not st.get("busy"):
                print("[wifi-portal] connected — stopping portal")
                # Brief window so phone can show success message
                time.sleep(3.0)
                httpd.shutdown()
                break
            # If connect failed, ensure hotspot is back for retry
            if st.get("ok") is False and not st.get("busy"):
                if not is_wifi_connected():
                    try:
                        ensure_hotspot()
                    except WifiError as e:
                        print(f"[wifi-portal] restart hotspot failed: {e}", file=sys.stderr)
            time.sleep(1.0)
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass

    if is_wifi_connected():
        stop_hotspot()
        print("[wifi-portal] done")
        exit_code = 0
    else:
        print("[wifi-portal] exited without client Wi‑Fi", file=sys.stderr)
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
