# 手办舞台



摄像头识别手办 → 豆包实时语音对话。树莓派 5 单文件运行，三种识别方案三选一。



## 硬件



| 组件 | 型号 |

|------|------|

| 主控 | 树莓派 5 |

| 摄像头 | IMX219（CSI，Picamera2） |

| 音频 | Jetson Nano USB 免驱麦克风 + 扬声器 |



## 三种主程序



| 文件 | 识别方式 | 需要 |

|------|----------|------|

| **`stage_yolo.py`** | 本地 YOLO（`toy.pt`） | 自训模型、ultralytics |

| **`stage_vision.py`** | 本地轻触发 + 云端 Doubao-VL | `ARK_API_KEY`、httpx |

| **`stage_feature.py`** | 本地轻触发 + DINOv2 特征匹配 | ONNX 模型、`registry/` |



## 快速开始（Pi · YOLO 方案）



```bash

cd figure-stage

python3 -m venv .venv

source .venv/bin/activate

pip install -r requirements.txt



cp .env.example .env

# 编辑 .env 填入 DOUBAO_APP_ID、DOUBAO_ACCESS_KEY、DOUBAO_APP_KEY



python stage_yolo.py

```



## 快速开始（Pi · 混合视觉方案）



```bash

pip install -r requirements.txt

cp .env.example .env

# 填入 DOUBAO_* 与 ARK_API_KEY



python stage_vision.py

```



启动后 **约 3 秒内保持展示台无手办**（采集背景 baseline）。



## 快速开始（Pi · DINOv2 特征方案）



### 1. 开发机导出 ONNX（仅需一次）



```bash

pip install torch

python scripts/export_dinov2_onnx.py

# 生成 models/dinov2_vits14.onnx，拷贝到树莓派

```



### 2. 树莓派注册手办特征



```bash

pip install -r requirements.txt

cp .env.example .env

# 填入 DOUBAO_*



python register_feature.py register --key ydog --name 小黄
python register_feature.py register --key wdog --name 小白
python register_feature.py register --key bubu --name Labubu

python register_feature.py list

python register_feature.py verify --key ydog

```



### 3. 运行



```bash

python stage_feature.py

```



注册与运行时 **机位、光线尽量一致**。若多只手办易混，可调 `.env` 中 `FEATURE_MIN_SCORE`、`FEATURE_MIN_MARGIN`，或重新 register 多采几帧。



开发机无摄像头时，可用图片目录注册：



```bash

python register_feature.py register --key wdog --name 小白 --image-dir ./captures/wdog

```



## 配置



| 位置 | 内容 |

|------|------|

| `.env` | 豆包 API 密钥、YOLO 路径 / 方舟视觉 / 特征阈值、USB 声卡设备号 |

| `stage_yolo.py` 顶部 | YOLO 检测阈值、三类手办 → 豆包人设 |

| `stage_vision.py` 顶部 | 画面变化阈值、视觉置信度 |

| `stage_feature.py` / `.env` | DINOv2 路径、registry、相似度阈值 |



## 手办识别（YOLO · `stage_yolo.py`）



| 项目 | 说明 |

|------|------|

| 基础模型 | YOLOv8n（`yolov8n.pt`） |

| 标注工具 | LabelImg |

| 训练数据 | 自行拍摄的三类手办样本 |

| 权重文件 | `toy.pt`（`YOLO_MODEL_PATH`） |



**三类目标**（`CLASS_IDX_TO_KEY` 顺序须与训练 class id 一致）：



| class id | 标签 | 角色 |

|----------|------|------|

| 0 | `wdog` | 小白（白色线条小狗） |

| 1 | `gaya` | 盖亚（盖亚奥特曼） |

| 2 | `bubu` | 布布（棕色拉布布精灵） |



## 混合识别（`stage_vision.py`）



无需 `toy.pt`：本地检测「有物体上台」→ 云端 VL 确认类别 → 豆包语音（人设与 YOLO 方案相同）。



开发机单张图测视觉：



```bash

pip install httpx opencv-python-headless

python test_vision_recognize.py --image your_figure.jpg

```



## 特征匹配（`stage_feature.py`）



| 项目 | 说明 |

|------|------|

| 特征模型 | DINOv2 ViT-S/14（`models/dinov2_vits14.onnx`） |

| 注册工具 | `register_feature.py` → `registry/{key}.npz` |

| 匹配方式 | 余弦相似度 + top1/top2 margin |

| 网络 | 识别阶段纯本地，仅语音走豆包 |



**register_feature.py 命令：**



| 命令 | 说明 |

|------|------|

| `register --key ydog --name 小黄` | 摄像头采帧注册 |
| `list` | 列出已注册类别 |
| `delete --key ydog` | 删除某类注册 |
| `verify --key ydog` | 单次匹配验证 |

合法 key：`bubu`、`sea`、`wdog`、`ydog`（支持对话中/结束后换娃开新会话）。



查看 USB 声卡编号：



```bash

python -c "import sounddevice; print(sounddevice.query_devices())"

```



## 开发机测试豆包 API



```bash

pip install websockets

python test_doubao_api.py

```



看到 `[OK] SessionStarted — API available` 即表示豆包 API 密钥与会话正常（不测麦克风）。

## 开发机完整语音测试（默认小白狗）

```bash

pip install websockets sounddevice scipy numpy

python test_doubao_voice.py

python test_doubao_voice.py --key wdog --device 0

```

对着麦克风说话，扬声器应听到小白自我介绍并回复。`Ctrl+C` 退出。



## 项目结构



```

figure-stage/

├── stage_yolo.py               # 方案 A：YOLO 本地识别

├── stage_vision.py             # 方案 B：轻触发 + 云端 VL

├── stage_feature.py            # 方案 C：轻触发 + DINOv2 特征

├── register_feature.py         # 特征注册 CLI

├── feature_embed.py            # DINOv2 ONNX + registry 公共逻辑

├── scripts/

│   └── export_dinov2_onnx.py   # 开发机导出 ONNX

├── requirements.txt

├── .env.example                # 复制为 .env 后填写密钥（.env 不进 git）

├── models/dinov2_vits14.onnx   # 导出后拷贝，不进 git

├── registry/                   # 注册特征，不进 git

└── toy.pt                      # 仅 stage_yolo.py 需要

```



## 上传 GitHub



```bash

# 确认 .env、docs/、test_*.py、recordings/、registry/、*.onnx 未被跟踪
git init
git add .
git status   # 不应出现 .env、docs/、test_*.py、recordings/、registry

git commit -m "Initial commit: figure-stage handoff voice platform"
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main

```



**提交前检查：** 代码中不含 API 密钥；Pi 本地 `.env` 仅留在设备上；若密钥曾写入代码或提交过 git，请在火山控制台轮换。



License: MIT


