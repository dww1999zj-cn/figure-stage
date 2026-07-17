#!/usr/bin/env bash
# Install Figure Stage Wi-Fi setup portal on Raspberry Pi OS (Bookworm+ / NetworkManager).
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请用 root 运行: sudo bash install.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${FS_WIFI_INSTALL_DIR:-/opt/figure-stage-wifi}"
SERVICE_NAME="figure-stage-wifi-portal.service"
UNIT_SRC="${SCRIPT_DIR}/${SERVICE_NAME}"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}"

echo "==> 检查 NetworkManager / nmcli"
if ! command -v nmcli >/dev/null 2>&1; then
  echo "未找到 nmcli。请先启用 NetworkManager，例如：" >&2
  echo "  sudo apt update && sudo apt install -y network-manager" >&2
  echo "  sudo systemctl enable --now NetworkManager" >&2
  exit 1
fi

if ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
  echo "NetworkManager 未运行，尝试启动…"
  systemctl enable --now NetworkManager || true
fi

echo "==> 安装到 ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/static"
install -m 0644 "${SCRIPT_DIR}/wifi_manager.py" "${INSTALL_DIR}/"
install -m 0644 "${SCRIPT_DIR}/portal_server.py" "${INSTALL_DIR}/"
install -m 0755 "${SCRIPT_DIR}/run_portal.py" "${INSTALL_DIR}/"
install -m 0644 "${SCRIPT_DIR}/static/index.html" "${INSTALL_DIR}/static/"
install -m 0644 "${SCRIPT_DIR}/README.md" "${INSTALL_DIR}/" 2>/dev/null || true

# Rewrite WorkingDirectory / ExecStart if custom install dir
sed "s|/opt/figure-stage-wifi|${INSTALL_DIR}|g" "${UNIT_SRC}" > "${UNIT_DST}"
chmod 0644 "${UNIT_DST}"

echo "==> 启用 systemd 服务"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
# Do not start now if already online — next boot will decide
echo "==> 完成"
echo "  热点 SSID: FigureStage-Setup"
echo "  热点密码: figurestage"
echo "  配网页:   http://10.42.0.1:8080/ （连上热点后）"
echo ""
echo "立即测试（会先等约 20s 已有 Wi‑Fi）:"
echo "  sudo systemctl start ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "禁用:"
echo "  sudo systemctl disable --now ${SERVICE_NAME}"
