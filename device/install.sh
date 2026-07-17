#!/usr/bin/env bash
# Install Figure Stage supervisor on Raspberry Pi OS (NetworkManager + venv).
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请用 root 运行: sudo bash install.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVICE_DIR="${FS_DEVICE_DIR:-${SCRIPT_DIR}}"
VENV_PY="${FS_PYTHON:-${DEVICE_DIR}/.venv/bin/python}"
SERVICE_NAME="figure-stage.service"
UNIT_SRC="${SCRIPT_DIR}/${SERVICE_NAME}"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}"

echo "==> 检查 NetworkManager / nmcli"
if ! command -v nmcli >/dev/null 2>&1; then
  echo "未找到 nmcli。请先: sudo apt install -y network-manager" >&2
  exit 1
fi

if [[ ! -x "${VENV_PY}" ]]; then
  echo "未找到 Python 虚拟环境: ${VENV_PY}" >&2
  echo "请先在 device 目录创建 venv 并 pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p "${DEVICE_DIR}/prompts" "${DEVICE_DIR}/.supervisor"

sed -e "s|/home/pi/figure-stage/device|${DEVICE_DIR}|g" \
    -e "s|/home/pi/figure-stage/device/.venv/bin/python|${VENV_PY}|g" \
    "${UNIT_SRC}" > "${UNIT_DST}"
chmod 0644 "${UNIT_DST}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo "==> 完成"
echo "  设备目录: ${DEVICE_DIR}"
echo "  启动:     sudo systemctl start ${SERVICE_NAME}"
echo "  日志:     sudo journalctl -u ${SERVICE_NAME} -f"
echo "  手动:     cd ${DEVICE_DIR} && ${VENV_PY} -m supervisor"
