# Figure Stage 开机热点配网

树莓派开机后若约 **20 秒内**未连上 Wi‑Fi，会自动开热点并提供网页配网；已联网则**不**进入配网模式。

依赖：**Raspberry Pi OS Bookworm+** 与 **NetworkManager**（`nmcli`）。不使用 hostapd/dnsmasq。

## 行为

1. systemd 启动本服务（`After=NetworkManager`）
2. 等待已有 Wi‑Fi（默认 20s）
3. 已连接 → 退出（exit 0）
4. 未连接 → 开热点 `FigureStage-Setup` / 密码 `figurestage`
5. 手机连热点，打开 `http://10.42.0.1:8080/`
6. 选择家用 SSID、填密码 → 提交
7. 保存 NM 连接（`autoconnect=yes`）、关热点、连上目标 Wi‑Fi

## 安装

在树莓派上（仓库内或拷贝本目录）：

```bash
cd /path/to/figure-stage/wifi_setup
sudo bash install.sh
```

默认安装到 `/opt/figure-stage-wifi`，并 `systemctl enable figure-stage-wifi-portal.service`。

自定义目录：

```bash
sudo FS_WIFI_INSTALL_DIR=/home/pi/figure-stage/wifi_setup bash install.sh
```

## 使用

1. 擦掉/禁用已知 Wi‑Fi 后重启（或 `sudo systemctl start figure-stage-wifi-portal`）
2. 手机搜索并连接热点 **FigureStage-Setup**（密码 `figurestage`）
3. 浏览器打开 **http://10.42.0.1:8080/**
4. 选自家 Wi‑Fi、输入密码、点连接
5. 热点消失后，Pi 应已连上家用网；再重启且家用网可用时**不会**再开热点

## 环境变量（可选）

在 systemd unit 的 `[Service]` 中加 `Environment=`，或：

| 变量 | 默认 | 说明 |
|------|------|------|
| `FS_WIFI_IFACE` | `wlan0` | 无线网卡 |
| `FS_HOTSPOT_SSID` | `FigureStage-Setup` | 热点名 |
| `FS_HOTSPOT_PASSWORD` | `figurestage` | 热点密码（≥8 位） |
| `FS_HOTSPOT_CONN` | `FigureStage-Hotspot` | NM 连接名 |
| `FS_WIFI_WAIT_SEC` | `20` | 开机等待已有 Wi‑Fi 秒数 |
| `FS_PORTAL_PORT` | `8080` | 配网页端口 |

修改 unit 后：`sudo systemctl daemon-reload && sudo systemctl restart figure-stage-wifi-portal`

## 排障

```bash
# 服务日志
sudo journalctl -u figure-stage-wifi-portal -e

# NetworkManager 是否在跑
systemctl status NetworkManager
nmcli general status
nmcli device status

# 手动起热点（调试）
sudo nmcli device wifi hotspot ifname wlan0 con-name FigureStage-Hotspot \
  ssid FigureStage-Setup password figurestage

# 看热点 IP（配网页地址）
nmcli -t -f IP4.ADDRESS connection show FigureStage-Hotspot
# 常见为 10.42.0.1

# 手动关热点
sudo nmcli connection down FigureStage-Hotspot
```

常见问题：

- **搜不到热点**：确认 `wlan0` 存在、未同时被其他程序占用；看 journal 是否有 `failed to start hotspot`。
- **打开不了 10.42.0.1**：确认手机连的是本热点；少数机型网关不是 `10.42.0.1`，用上面 `nmcli` 查实际 IP。
- **扫不到附近 Wi‑Fi**：部分芯片在 AP 模式下扫描受限，用页面「手动输入 SSID」。
- **连接失败**：密码错误或 5GHz-only 等；改完后服务会尽量重新开热点供重试。
- **正常开机仍进热点**：家用连接未 `autoconnect`，或等待时间太短；可增大 `FS_WIFI_WAIT_SEC`，并检查 `nmcli connection show`。

## 与主程序

本服务**独立**于 `stage_feature.py`。舞台程序可另行在联网后再启动，或在其 systemd unit 中 `After=network-online.target` / 本服务。

## 卸载

```bash
sudo systemctl disable --now figure-stage-wifi-portal.service
sudo rm -f /etc/systemd/system/figure-stage-wifi-portal.service
sudo systemctl daemon-reload
sudo rm -rf /opt/figure-stage-wifi
```
