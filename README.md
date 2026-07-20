# 手办舞台 Figure Stage（设备端）

桌面固定摄像头识别手办 → **云端**算视觉特征 → **本机**连豆包 Realtime 开口对话。

**豆包 / 瑞幸等密钥只保存在设备本地**（门户网页填写），不会上传到识别云。

<p align="center">
  <img src="assets/figure-stage-promo.jpg" width="900" alt="手办舞台 Figure Stage" />
</p>

<p align="center">
  <img src="assets/stage-empty.jpg" width="480" alt="实物空舞台" />
</p>

---

## 架构（必读）

```
┌─────────────────────────────────────────────────────────────┐
│  树莓派 device/                                              │
│  supervisor（systemd）                                        │
│    ├─ 热点配网 + HTTP 门户 :8080                              │
│    ├─ 提示音 prompts/*.wav                                    │
│    └─ 自动启停 stage/run_stage.py                             │
│         ├─ 画面变化 / 启动扫描 → 云识别                        │
│         ├─ 豆包 Realtime 对话（密钥本地）                       │
│         └─ 名字唤醒：本地 VAD + 豆包短句 ASR（轻流量）          │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTPS + Bearer Token
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  识别云服务（由运营方提供 URL 与 Token）                        │
│  仅存视觉特征 / 清单；不存豆包密钥、不存人设全文                │
└─────────────────────────────────────────────────────────────┘
```

| 数据 | 存在哪 |
|------|--------|
| 视觉特征、手办名、音色预设 | 识别云（按 `DEVICE_ID` 隔离） |
| 人设 persona | 本机 `device/figures_local.json` |
| 豆包 / 云 Token / DEVICE_ID | 本机 `device/config.env` |

---

## 仓库结构

| 路径 | 说明 |
|------|------|
| **[`device/`](device/)** | 门户 + 监督进程 + 薄舞台 + systemd（主目录） |
| [`legacy/`](legacy/) | 旧本机识别原型归档，勿当新装路径 |
| [`assets/`](assets/) | 宣传图 |

日常只操作 **`device/`**。从 SD 卡到开机自启的细节与排障见 **[`device/README.md`](device/README.md)**。

---

## 你需要准备什么

### 软件 / 账号

1. 运营方提供的 **`CLOUD_BASE_URL`** + **`DEVICE_CLOUD_TOKEN`**（与云端约定一致，**勿自造**）
2. 豆包 Realtime：`DOUBAO_APP_ID` / `ACCESS_KEY` / `APP_KEY`
3. （可选）提示音 WAV：见 `device/prompts/README.md`

### 硬件（已验证：Pi 5）

- Raspberry Pi 5 + 官方或兼容电源
- CSI 摄像头 **imx219**（需改 `config.txt`，见 `device/README.md`）
- USB 声卡（播放 + 麦克；勿默认走 HDMI）
- 可选：网线（断 Wi‑Fi 测热点时仍能 SSH）

**舞台内部接线（参考）**

<p align="center">
  <img src="assets/stage-inside.jpg" width="520" alt="舞台底座内部：树莓派、摄像头、USB 声卡与扬声器接线" />
</p>

| 部件 | 接法 |
|------|------|
| 树莓派 | USB-C 供电 |
| IMX219 摄像头 | CSI 排线 → Pi 摄像头接口 |
| USB 声卡 | 插入 Pi USB 口（`config.env` 中 `AUDIO_DEVICE_ID` 选此设备） |
| 麦克风 | 接 USB 声卡输入 |
| 扬声器 | 红黑线 → USB 声卡 / 功放输出 |
| 舞台架 | 3D 打印；如需同款建模文件，可联系微信：alex_198888 |

---

## 快速路径（已有云地址 + 已刷系统）

```bash
cd device
python3 -m venv .venv && source .venv/bin/activate
# 大包优先 apt，再 pip 小包 —— 详见 device/README.md
pip install -r requirements.txt
cp config.example.env config.env   # 或稍后用门户填写

# 自备 prompts/*.wav 后：
sudo bash install.sh
sudo systemctl enable --now figure-stage
```

门户（已联网）：**http://figure-stage.local:8080/**  
状态：`GET /api/status` → `phase` 应为 `stage_ready`。

完整从 SD 卡到自启：**务必读 [`device/README.md`](device/README.md)**。

---

## 运行时行为摘要

1. **开机** → `figure-stage.service` → `python -m supervisor`
2. 等 Wi‑Fi（默认 20s）→ 无网则开热点 `FigureStage-Setup` / `figurestage`，门户 `http://10.42.0.1:8080/`
3. 无网提示音 **同一次离线只播一次**（不是循环念）
4. 凭证 + ≥1 手办 → 自动起舞台；注册后会空台等待 + 启动扫描
5. 聊完手办不移开 → **喊注册名**唤醒（不上 60s 视觉自动再开聊）；空台 / 未识别物体喊名字无效

---

## 与识别云的约定

- 请求头：`Authorization: Bearer <DEVICE_CLOUD_TOKEN>`
- `DEVICE_ID`：门户首次自动生成并写入本机
- 云地址与 Token 由运营方下发；设备只负责调用

---

## 许可

| 文档 | 说明 |
|------|------|
| [LICENSE](LICENSE) | 非商业免费 |
| [COMMERCIAL.md](COMMERCIAL.md) | 商用联系 dww1999zj@gmail.com |
| [TRADEMARK.md](TRADEMARK.md) | 项目名称规则 |

许可人：**dww1999zj-cn**
