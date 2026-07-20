# Figure Stage — 设备端复刻与运维手册

本文面向：**从新 SD 卡装到 systemd 自启**、以及日后自己回顾。  
密钥只存在本机 `config.env` / 门户；识别走运营方云地址。

---

## 1. 目录结构（只认这些）

```
device/
├── config.example.env      # 模板 → 复制为 config.env（勿提交）
├── config.env              # 运行时配置（gitignore）
├── figures_local.json      # 人设本机库（gitignore）
├── requirements.txt
├── install.sh              # 安装 systemd
├── figure-stage.service
├── prompts/                # 提示音 WAV（随仓库）
│   └── README.md
├── portal/                 # HTTP 门户 :8080
│   ├── server.py
│   ├── wifi_manager.py     # nmcli 热点 / 配网
│   ├── config_store.py
│   ├── figure_store.py
│   ├── camera_capture.py
│   └── static/             # index / wifi / credentials / figures
├── stage/                  # 薄舞台
│   ├── run_stage.py        # 识别触发 + 豆包对话 + 名字唤醒挂钩
│   ├── cloud_client.py
│   ├── wake_listener.py    # 共用麦队列 + VAD
│   ├── doubao_wake_asr.py  # 短句 ASR
│   └── doubao_dialog.py / luckin_*
└── supervisor/             # 监督进程（推荐入口）
    ├── __main__.py         # python -m supervisor
    ├── readiness.py        # /api/status 同源
    ├── control.py          # 摄像头让位 IPC
    └── prompt_player.py
```

**入口**

| 场景 | 命令 |
|------|------|
| 生产 | `sudo systemctl start figure-stage`（`python -m supervisor`） |
| 调试门户 | `python portal/server.py` |
| 调试舞台 | 先停 systemd，再 `python stage/run_stage.py` |

**不要**用仓库根目录的旧 `wifi_setup/`、`stage_feature.py` 等。

---

## 2. 硬件与系统（Pi 5 已验证）

### 2.1 摄像头 imx219（必做）

`No cameras available` 时几乎都是 overlay 不对。编辑：

```bash
sudo nano /boot/firmware/config.txt
```

确保类似：

```ini
camera_auto_detect=0
dtoverlay=imx219,cam0
```

重启后检查：

```bash
rpicam-hello --list-cameras
# 应看到 imx219
```

`config.txt` 在系统分区，**删掉 `device/` 文件夹不会丢**。

### 2.2 USB 声卡（必做）

```bash
aplay -l
# 记下 USB 卡号，例如 card 2: UACDemoV1.0
```

固定默认输出（示例 card **2**）：

```bash
sudo tee /etc/asound.conf <<'EOF'
defaults.pcm.card 2
defaults.ctl.card 2
EOF
aplay /usr/share/sounds/alsa/Front_Center.wav
```

舞台里 **`AUDIO_DEVICE_ID`** 是 **sounddevice 设备编号**（不一定等于 ALSA card 号）：

```bash
cd ~/figure-stage/device && source .venv/bin/activate
python -c "import sounddevice as sd; print(sd.query_devices())"
```

在门户「凭证」里填对应编号并保存。

### 2.3 网络

- 需要 **NetworkManager + nmcli**（热点依赖）
- Hostname 建议：`figure-stage` → 门户 `http://figure-stage.local:8080/`
- Bookworm 默认 **WayVNC** 时，RealVNC「传文件」可能灰色 → 用 **SCP / WinSCP**，不必为传文件切 X11

---

## 3. 软件安装（新机清单）

### 3.1 系统包

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip \
  python3-picamera2 python3-opencv python3-numpy python3-scipy \
  network-manager alsa-utils
sudo systemctl enable --now NetworkManager
```

### 3.2 代码与 venv

**注意：** Windows 上的 `.venv` **不能** scp 到 Pi。删掉 `device/` 重建时，**系统 apt 还在，只需重建 venv**。

```bash
# 例：从开发机同步（不要带 Windows 的 .venv）
# scp -r device pi@figure-stage.local:~/figure-stage/

cd ~/figure-stage/device
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate

# 让 venv 能用 apt 的 picamera2
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "/usr/lib/python3/dist-packages" > ".venv/lib/python${PYVER}/site-packages/system-packages.pth"

# 推荐：大包用 apt，只 pip 轻量包（快）
pip install httpx sounddevice websockets -i https://pypi.tuna.tsinghua.edu.cn/simple
# 或：pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

python -c "import picamera2, cv2, httpx, sounddevice, websockets; print('OK')"
```

### 3.3 提示音

仓库已含 `device/prompts/*.wav`（随代码同步即可）。文件名：

| 文件 | 何时 |
|------|------|
| `network_offline.wav` | 无 Wi‑Fi（**同一次离线只播 1 次**） |
| `network_connected_register.wav` | 已联网、凭证齐、尚未注册手办 |
| `stage_ready.wav` | 可选，全部就绪 |
| `stage_empty_baseline.wav` | 可选，舞台采空台前 |

见 `prompts/README.md`。

### 3.4 配置

```bash
cp config.example.env config.env
# 或完全靠门户填写（推荐）
```

| 变量 | 说明 |
|------|------|
| `CLOUD_BASE_URL` | 运营方云 HTTPS |
| `DEVICE_CLOUD_TOKEN` | = 云 `CLOUD_API_TOKEN`，**运营方给，不要设备自造** |
| `DEVICE_ID` | 首次打开门户自动生成 |
| `DOUBAO_*` | 豆包 Realtime |
| `AUDIO_DEVICE_ID` | sounddevice 索引 |
| `FS_NAME_WAKE` | 名字唤醒，默认 true |
| `FS_STARTUP_SCAN` | 启动后扫一次台上是否已有手办 |
| `SESSION_IDLE_SECONDS` | 对话内静音挂断（默认 60）；**聊完不移开不再靠视觉 60s 再开聊** |

---

## 4. 监督进程与 systemd

### 4.1 手动跑（调试）

```bash
cd ~/figure-stage/device
source .venv/bin/activate
# 不要与 systemd 同时跑
sudo systemctl stop figure-stage 2>/dev/null || true
python -m supervisor
```

流程：等 Wi‑Fi → 无网开热点 → 门户线程 → 就绪则自动 `run_stage.py`。

**默认无 OpenCV 预览**，无需 `unset DISPLAY`。若要 VNC 看画面：`FS_SHOW_PREVIEW=true`。

### 4.2 开机自启

```bash
cd ~/figure-stage/device
# 先停掉手动的 supervisor / run_stage
pkill -f run_stage.py 2>/dev/null || true
pkill -f "python -m supervisor" 2>/dev/null || true

sudo bash install.sh
sudo systemctl enable --now figure-stage
sudo systemctl status figure-stage --no-pager
```

服务以 **root** 跑（便于 nmcli 热点）。日志：

```bash
sudo journalctl -u figure-stage -f
```

重启验证：

```bash
sudo reboot
# 起来后：
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool
# 期望 phase=stage_ready（已配好凭证与手办时）
```

常用：`restart` / `stop` / `disable`。

---

## 5. 门户使用（两个网络阶段，一个门户）

| 阶段 | 怎么打开 |
|------|----------|
| Pi 未连家里 Wi‑Fi（热点） | 手机连 `FigureStage-Setup` / `figurestage` → **http://10.42.0.1:8080/** |
| Pi 已联网 | **http://figure-stage.local:8080/** 或 `http://<局域网IP>:8080/` |

配网成功后热点会关，手机须改连**同一家里 Wi‑Fi**，再用 `.local` 地址（成功页会大字提示）。

| 页面 | 作用 |
|------|------|
| `/` | 状态向导 + 下一步 |
| `/wifi` | 配网 |
| `/credentials` | 云 Token、豆包等（仅存本机） |
| `/figures` | 摄像头注册；人设本机；**名称 = 唤醒词** |
| `GET /api/status` | JSON 就绪状态 |

### 加新手办（日常）

1. 监督进程在跑即可  
2. 打开 `/figures` → 填名称 / 音色 / 人设 → 手办放镜头前 → 注册  
3. 门户会请求停舞台 → 采帧 → 再自动拉起舞台  
4. 注册成功后尽量**移开手办约 10 秒**让空台 baseline 更干净  

同一 `DEVICE_ID` 可注册多个；云按设备隔离。

### 方案 A：整机当「新设备」重测

```bash
cd ~/figure-stage/device
sudo systemctl stop figure-stage
rm -f config.env figures_local.json .camera.lock
rm -rf .supervisor
cp config.example.env config.env
# 再 start 或 python -m supervisor，门户会生成新 DEVICE_ID
```

云上旧 `DEVICE_ID` 下的手办仍在，互不影响。

---

## 6. 舞台与名字唤醒

### 6.1 视觉逻辑

1. 启动：可选等待空台 → 采 baseline → **启动扫描**一次  
2. 空台 → 放手办：diff 触发 → 云识别 → 开聊  
3. 对话中换娃：视觉切换  
4. 聊完**不移开**：不再 60s 视觉自动再开 → **喊名字**  

### 6.2 名字唤醒（轻流量）

- 空闲：本地能量 VAD，**不上云**  
- 检测到说话：共用麦克风队列（**不再开第二个 InputStream**，避免 USB 麦 `Device unavailable`）→ 上传约 2～4s 给豆包短句 ASR  
- **仅台上已识别的注册手办名**可唤醒；空台 / 未匹配物体 / 喊错名 → 忽略  

```bash
# config.env
FS_NAME_WAKE=true
```

日志关键字：`[wake] 当前可唤醒: 小白` → `ASR` → 再开聊。

### 6.3 摄像头互斥

- `.camera.lock` + `.supervisor/camera_request`  
- 注册时 supervisor 停 stage；**不要**同时手动开两个占摄像头的进程  
- 报错 `Pipeline handler in use`：`pkill -f run_stage` → 等 3s → 清 `.camera.lock` → 再起  

---

## 7. 同步代码注意项

| 做法 | 说明 |
|------|------|
| 只同步改过的目录 | `portal/` `stage/` `supervisor/` |
| **不要**从 Windows 覆盖 Pi 的 `.venv` | Pi 需 Linux venv |
| **不要**随意覆盖 Pi 的 `config.env` | 里面有 Token / DEVICE_ID |
| `config.example.env` | 可覆盖，作模板参考 |
| 提示音 | 已在 `device/prompts/`，随代码同步 |

---

## 8. 排障速查

| 现象 | 处理 |
|------|------|
| `No cameras available` | `dtoverlay=imx219,cam0`，`rpicam-hello --list-cameras` |
| `aplay` / 提示音无声 / Device busy | `/etc/asound.conf` 指 USB；舞台提示音已改走播放线程 |
| 名字唤醒 `Device unavailable` | 需含「共用麦队列」的新版 `wake_listener.py` + `run_stage.py` |
| `low_confidence` 刷屏 | 清台面后 `systemctl restart figure-stage` 重采 baseline；或提高 `FEATURE_TRIGGER_DIFF_ON` |
| `figure-stage.local` 打不开 | 用 `hostname -I` 的 IP；确认同网段 |
| 门户乱码日志 | SSH `LANG=zh_CN.UTF-8`，不影响功能 |
| VNC 传文件灰色 | WayVNC 限制；用 SCP |
| pip 很慢 | apt 大包 + 清华镜像只装 httpx/sounddevice/websockets |
| systemd 与手动冲突 | 只保留一种：`systemctl` **或** 前台 `python -m supervisor` |
| 热点测完 SSH 断 | 电脑连热点后 `ssh pi@10.42.0.1`；或提前 `tee` 日志 |

健康检查：

```bash
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool
# wifi / credentials_ok / figures_ok / stage_ready / phase
ps aux | grep -E 'supervisor|run_stage' | grep -v grep
```

---

## 9. 从零复刻检查清单

- [ ] 刷 Raspberry Pi OS，用户 `pi`，hostname `figure-stage`
- [ ] `config.txt`：imx219；`rpicam-hello` 可见
- [ ] NetworkManager；USB 声卡 + `asound.conf` + `AUDIO_DEVICE_ID`
- [ ] 部署 `device/`；建 **Pi 本地** `.venv` + system-packages.pth
- [ ] `prompts/*.wav` 已随仓库就位
- [ ] 运营方下发云 URL + Token；门户填豆包
- [ ] 注册 ≥1 手办（名称短清晰）
- [ ] `sudo bash install.sh` && `enable --now figure-stage`
- [ ] `reboot` 后 `/api/status` → `stage_ready`
- [ ] 上台对话；聊完喊名字唤醒

---

## 10. 相关

- 提示音说明：`prompts/README.md`
- 仓库总览：[../README.md](../README.md)
