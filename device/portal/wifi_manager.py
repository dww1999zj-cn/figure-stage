"""NetworkManager (nmcli) helpers for Pi Wi-Fi setup portal."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass


DEFAULT_IFACE = os.environ.get("FS_WIFI_IFACE", "wlan0")
HOTSPOT_SSID = os.environ.get("FS_HOTSPOT_SSID", "FigureStage-Setup")
HOTSPOT_PASSWORD = os.environ.get("FS_HOTSPOT_PASSWORD", "figurestage")
HOTSPOT_CONN = os.environ.get("FS_HOTSPOT_CONN", "FigureStage-Hotspot")
CLIENT_CONN_PREFIX = os.environ.get("FS_WIFI_CONN_PREFIX", "FigureStage-WiFi")


class WifiError(RuntimeError):
    pass


@dataclass
class WifiNetwork:
    ssid: str
    signal: int
    security: str


def _run(args: list[str], *, check: bool = True, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise WifiError("未找到 nmcli，请确认已安装 NetworkManager") from e
    except subprocess.TimeoutExpired as e:
        raise WifiError(f"命令超时: {' '.join(args)}") from e


def _nmcli(*args: str, check: bool = True, timeout: float = 60.0) -> str:
    proc = _run(["nmcli", *args], check=False, timeout=timeout)
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise WifiError(err or f"nmcli 失败: {' '.join(args)}")
    return (proc.stdout or "").strip()


def wifi_iface() -> str:
    return DEFAULT_IFACE


def is_wifi_connected(iface: str | None = None) -> bool:
    """True if wlan has an active client connection (not AP/hotspot)."""
    iface = iface or wifi_iface()
    out = _nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", check=False)
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        dev, typ, state, conn = parts[0], parts[1], parts[2], parts[3]
        if dev != iface or typ != "wifi":
            continue
        if state != "connected":
            return False
        # Hotspot connection name should not count as "online home wifi"
        if conn in (HOTSPOT_CONN, "Hotspot") or conn.startswith("Hotspot"):
            return False
        if HOTSPOT_SSID and conn == HOTSPOT_SSID:
            return False
        return bool(conn)
    return False


def wait_for_wifi(timeout_sec: float = 20.0, iface: str | None = None) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if is_wifi_connected(iface):
            return True
        time.sleep(1.0)
    return is_wifi_connected(iface)


def hotspot_gateway_ip() -> str:
    """Best-effort gateway IP for NM shared hotspot (often 10.42.0.1)."""
    iface = wifi_iface()
    # Prefer IP on the hotspot connection
    out = _nmcli("-t", "-f", "IP4.ADDRESS", "connection", "show", HOTSPOT_CONN, check=False)
    ip = _parse_nmcli_ipv4(out)
    if ip:
        return ip
    out = _nmcli("-t", "-f", "IP4.ADDRESS", "device", "show", iface, check=False)
    ip = _parse_nmcli_ipv4(out)
    if ip:
        return ip
    return "10.42.0.1"


def _parse_nmcli_ipv4(out: str) -> str | None:
    for line in out.splitlines():
        if ":" in line:
            _, val = line.split(":", 1)
        else:
            val = line
        m = re.match(r"(\d+\.\d+\.\d+\.\d+)", val.strip())
        if m:
            return m.group(1)
    return None


def wlan_client_ip(iface: str | None = None) -> str | None:
    """IPv4 on wlan when connected as STA (home Wi‑Fi)."""
    iface = iface or wifi_iface()
    if not is_wifi_connected(iface):
        return None
    out = _nmcli("-t", "-f", "IP4.ADDRESS", "device", "show", iface, check=False)
    return _parse_nmcli_ipv4(out)


def device_hostname() -> str:
    return os.environ.get("FS_DEVICE_HOSTNAME", "").strip() or socket.gethostname()


def portal_urls() -> dict:
    """URLs for portal UI — one entry point, different network phases."""
    port = int(os.environ.get("FS_PORTAL_PORT", "8080"))
    host = device_hostname()
    home_url = f"http://{host}.local:{port}/"
    hotspot_url = f"http://{hotspot_gateway_ip()}:{port}/"
    ip = wlan_client_ip()
    lan_ip_url = f"http://{ip}:{port}/" if ip else None
    wifi = is_wifi_connected()
    preferred_url = home_url if wifi else hotspot_url
    return {
        "hostname": host,
        "port": port,
        "hotspot_ssid": HOTSPOT_SSID,
        "hotspot_password": HOTSPOT_PASSWORD,
        "hotspot_url": hotspot_url,
        "home_url": home_url,
        "lan_ip_url": lan_ip_url,
        "preferred_url": preferred_url,
    }


def ensure_hotspot(iface: str | None = None) -> None:
    iface = iface or wifi_iface()
    # If our hotspot already up, done
    active = _nmcli("-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active", check=False)
    for line in active.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[0] == HOTSPOT_CONN and parts[1] == iface:
            return

    # Remove stale profile with same name then recreate via hotspot helper
    _nmcli("connection", "delete", HOTSPOT_CONN, check=False)
    # Official shortcut creates a secure AP + shared IPv4 DHCP
    _nmcli(
        "device",
        "wifi",
        "hotspot",
        "ifname",
        iface,
        "con-name",
        HOTSPOT_CONN,
        "ssid",
        HOTSPOT_SSID,
        "password",
        HOTSPOT_PASSWORD,
    )
    _nmcli(
        "connection",
        "modify",
        HOTSPOT_CONN,
        "connection.autoconnect",
        "no",
        check=False,
    )


def stop_hotspot() -> None:
    _nmcli("connection", "down", HOTSPOT_CONN, check=False)
    # Also try generic Hotspot name NM sometimes uses
    _nmcli("connection", "down", "Hotspot", check=False)


def scan_wifi(iface: str | None = None) -> list[WifiNetwork]:
    iface = iface or wifi_iface()
    # Rescan (may fail while in AP mode on some chips — ignore)
    _nmcli("device", "wifi", "rescan", "ifname", iface, check=False, timeout=30.0)
    time.sleep(1.5)
    out = _nmcli(
        "-t",
        "-f",
        "SSID,SIGNAL,SECURITY",
        "device",
        "wifi",
        "list",
        "ifname",
        iface,
        check=False,
    )
    networks: dict[str, WifiNetwork] = {}
    for line in out.splitlines():
        # nmcli -t escapes ':' as '\:'
        parts = re.split(r"(?<!\\):", line)
        parts = [p.replace("\\:", ":") for p in parts]
        if len(parts) < 3:
            continue
        ssid, signal_s, security = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not ssid or ssid == HOTSPOT_SSID:
            continue
        try:
            signal = int(signal_s)
        except ValueError:
            signal = 0
        prev = networks.get(ssid)
        if prev is None or signal > prev.signal:
            networks[ssid] = WifiNetwork(ssid=ssid, signal=signal, security=security or "")
    return sorted(networks.values(), key=lambda n: n.signal, reverse=True)


def _client_conn_name(ssid: str) -> str:
    safe = re.sub(r"[^\w.\-]+", "_", ssid)[:48]
    return f"{CLIENT_CONN_PREFIX}-{safe}"


def connect_wifi(ssid: str, password: str, iface: str | None = None) -> None:
    """Save credentials and connect; stop hotspot afterward."""
    iface = iface or wifi_iface()
    ssid = (ssid or "").strip()
    if not ssid:
        raise WifiError("SSID 不能为空")

    conn_name = _client_conn_name(ssid)

    # Tear down AP so wlan0 can join STA mode
    stop_hotspot()
    time.sleep(1.0)

    # Delete previous profile with same name to avoid stale PSK
    _nmcli("connection", "delete", conn_name, check=False)

    args = [
        "connection",
        "add",
        "type",
        "wifi",
        "ifname",
        iface,
        "con-name",
        conn_name,
        "ssid",
        ssid,
        "wifi-sec.key-mgmt",
        "wpa-psk" if password else "none",
        "ipv4.method",
        "auto",
        "ipv6.method",
        "auto",
        "connection.autoconnect",
        "yes",
        "connection.autoconnect-priority",
        "100",
    ]
    if password:
        args.extend(["wifi-sec.psk", password])
    _nmcli(*args)

    try:
        _nmcli("connection", "up", conn_name, timeout=45.0)
    except WifiError:
        # Fallback: device wifi connect
        if password:
            _nmcli(
                "device",
                "wifi",
                "connect",
                ssid,
                "password",
                password,
                "ifname",
                iface,
                "name",
                conn_name,
                timeout=45.0,
            )
        else:
            _nmcli(
                "device",
                "wifi",
                "connect",
                ssid,
                "ifname",
                iface,
                "name",
                conn_name,
                timeout=45.0,
            )

    # Give DHCP a moment
    for _ in range(15):
        if is_wifi_connected(iface):
            stop_hotspot()
            return
        time.sleep(1.0)

    # Re-raise so portal can restart hotspot
    raise WifiError(f"已保存配置，但未能连上 Wi‑Fi「{ssid}」，请检查密码后重试")
