# 手办舞台 Figure Stage

树莓派 5 桌面手办互动：摄像头识别展示台上的手办，Realtime 端到端语音对话。

---

## 这是什么？

「手办舞台」是一套跑在 **树莓派 5** 上的桌面互动装置：摄像头对准展示台，USB 麦克风与扬声器负责听和说。手办 **放上台** 后系统本地识别是哪一款；识别成功后开启 **Realtime 端到端语音**——能听、能说、可配置音色与人设，支持即兴对话。

**一句话：把静态手办展示，升级成可对话的互动舞台。**

---

## 产品形态

```
   ┌─────────────── 摄像头（俯视展示台）───────────────┐
   │                                                   │
   │              ■  ← 手办放这里，即触发              │
   │           ┌─────────┐                             │
   │           │  小舞台  │                             │
   │           └─────────┘                             │
   └───────────────────────────────────────────────────┘
          🎤 USB 麦克风          🔊 扬声器
                    │
              树莓派 5（本地识别 + 云端语音）
```

- **硬件形态**：树莓派 + IMX219 摄像头 + 小展示台 + USB 音频，适合书桌、展台、收藏展示角
- **交互形态**：无需按钮——**上台即唤醒**，下台或超时结束；更换手办后自动切换对应人设与音色
- **软件形态**：Python 单入口 `stage_feature.py`，配置集中在 `.env`，密钥不进源码

---

## 核心功能

| 能力 | 说明 |
|------|------|
| **看见** | 摄像头检测「台上是否有物体」，空台 baseline + 轻量触发 |
| **认出** | 本地 **DINOv2** 特征匹配，识别是哪一款手办（无需云端识图 API） |
| **开口** | Realtime WebSocket：ASR + 对话 + TTS 流式输出 |
| **换角** | 每只手办可绑定独立人设、音色与说话风格；对话中更换手办可开新会话 |
| **注册** | `register_feature.py` 采帧注册，支持 Pi 摄像头或开发机图片目录 |

每只手办在 `CHARACTER_CONFIG` 中单独配置 prompt、speaker、speaking_style，可按需扩展。

---

## 创意与亮点

- **直觉交互**：展示台前的手办常让人想「跟它说句话」——项目把这份习惯做成可运行系统
- **本地识别、云端语音**：手办识别在 Pi 上完成，延迟低、不依赖识图 API；仅对话链路走云端
- **一办一人格**：不是同一套 TTS 换皮，而是每只手办独立的人设与音色
- **可 DIY 的开源方案**：自行注册新手办特征、调整人设、搭建展台；非商业使用免费（见 [LICENSE](LICENSE)）

---

## 技术路径

本仓库采用 **DINOv2 特征注册 + 本地匹配 + Realtime 语音** 完整方案：

`export ONNX` → `register_feature.py` 注册 → `stage_feature.py` 上台识别并聊天

---

## 硬件

| 组件 | 型号 |
|------|------|
| 主控 | 树莓派 5 |
| 摄像头 | IMX219（CSI，Picamera2） |
| 音频 | USB 免驱麦克风 + 扬声器 |

## 快速开始

### 1. 开发机导出 ONNX（仅需一次）

```bash
cd figure-stage
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install torch
python scripts/export_dinov2_onnx.py
# 生成 models/dinov2_vits14.onnx，拷贝到树莓派（如 ~/Desktop/）
```

### 2. 树莓派注册手办特征

```bash
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env：DOUBAO_* 与 FEATURE_MODEL_PATH

python register_feature.py register --key wdog --name 手办A
python register_feature.py register --key ydog --name 手办B

python register_feature.py list
python register_feature.py verify --key wdog
```

注册与运行时 **机位、光线尽量一致**。若多只手办易混，可调 `.env` 中 `FEATURE_MIN_SCORE`、`FEATURE_MIN_MARGIN`，或重新 register 多采几帧。

开发机无摄像头时，可用图片目录注册：

```bash
python register_feature.py register --key wdog --name 手办A --image-dir ./captures/figure_a
```

### 3. 运行（识别 + 聊天）

```bash
python stage_feature.py
```

启动后 **约 3 秒内保持展示台无手办**（采集背景 baseline）。手办上台后本地 DINOv2 匹配，命中则开启 Realtime 语音对话；对话中更换手办可开新会话。

查看 USB 声卡编号：

```bash
python -c "import sounddevice; print(sounddevice.query_devices())"
```

## 配置

| 位置 | 内容 |
|------|------|
| `.env` | 豆包 API 密钥、DINOv2 路径、`registry/`、特征阈值、USB 声卡设备号 |
| `stage_feature.py` 顶部 | 各手办 key → 语音人设（`CHARACTER_CONFIG`） |

常用环境变量见 [.env.example](.env.example)。

## register_feature.py 命令

| 命令 | 说明 |
|------|------|
| `register --key wdog --name 手办A` | 摄像头采帧注册 |
| `register --key wdog --name 手办A --image-dir ./captures/figure_a` | 从目录读图注册 |
| `list` | 列出已注册类别 |
| `delete --key wdog` | 删除某类注册 |
| `verify --key wdog` | 单次匹配验证 |

允许的 key 在代码中配置（见 `register_feature.py`、`CHARACTER_CONFIG`）；支持对话中或结束后更换手办并开新会话。

## 特征匹配（`stage_feature.py`）

| 项目 | 说明 |
|------|------|
| 特征模型 | DINOv2 ViT-S/14（`models/dinov2_vits14.onnx`） |
| 注册工具 | `register_feature.py` → `registry/{key}.npz` |
| 匹配方式 | 余弦相似度 + top1/top2 margin |
| 上台检测 | 相对空台 baseline 的灰度差轻触发 |
| 网络 | **识别阶段纯本地**，仅语音对话走云端 |

## 项目结构

```
figure-stage/
├── stage_feature.py            # 主程序：轻触发 + DINOv2 匹配 + Realtime 语音
├── register_feature.py         # 特征注册 CLI
├── feature_embed.py            # DINOv2 ONNX + registry 公共逻辑
├── doubao_dialog.py            # Realtime dialog 构建
├── scripts/
│   └── export_dinov2_onnx.py   # 开发机导出 ONNX
├── requirements.txt
├── .env.example                # 复制为 .env 后填写（.env 不进 git）
├── models/dinov2_vits14.onnx   # 导出后拷贝，不进 git
└── registry/                   # 注册特征，不进 git
```

## 上传 GitHub

```bash
# 确认 .env、docs/、test_*.py、recordings/、registry/、*.onnx 未被跟踪
git init
git add .
git status   # 不应出现 .env、docs/、test_*.py、recordings/、registry

git commit -m "Initial commit: figure-stage DINOv2 register and voice"
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```

**提交前检查：** 代码中不含 API 密钥；Pi 本地 `.env` 仅留在设备上；若密钥曾写入代码或提交过 git，请在火山控制台轮换。

## 许可与商用

| 文档 | 说明 |
|------|------|
| [LICENSE](LICENSE) | **非商业使用免费**；修改与 fork 须保留许可与版权声明 |
| [COMMERCIAL.md](COMMERCIAL.md) | **商业使用** 须事先邮件联系 **dww1999zj@gmail.com** 取得书面许可 |
| [TRADEMARK.md](TRADEMARK.md) | 「手办舞台 / Figure Stage」名称不随代码许可转让 |

许可人：**dww1999zj-cn**
