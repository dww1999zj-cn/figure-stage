# 手办舞台 Figure Stage

树莓派 5 桌面手办互动：摄像头识别展示台上的手办，Realtime 语音对话与唱歌。

<p align="center">
  <img src="assets/figure-stage-promo.jpg" width="900" alt="手办舞台 Figure Stage 产品宣传图" />
</p>

手办放上台 → **本地认出是哪一款** → **开口对话 / 唱歌**。识别不联网，只有语音走 Realtime API。

<p align="center">
  <img src="assets/stage-empty.jpg" width="480" alt="实物展台" />
</p>

---

## 怎么用（三步）

```
① 准备：Pi + 摄像头 + 麦克风音箱 + ONNX 模型 + 语音 API 密钥
② 注册：register_feature.py 为每只手办采帧，写入 registry/
③ 运行：stage_feature.py — 空台几秒后，手办上台即识别并聊天
```

---

## 需要什么

**硬件（树莓派 5 上运行）**

- 树莓派 5
- IMX219 摄像头（CSI）
- USB 麦克风 + 扬声器
- 小展示台（固定机位）
- 开发机（可选）：导出 ONNX；或用图片目录注册

**舞台内部接线（参考）**

<p align="center">
  <img src="assets/stage-inside.jpg" width="520" alt="舞台底座内部：树莓派、摄像头、USB 声卡与扬声器接线" />
</p>

| 部件 | 接法 |
|------|------|
| 树莓派 | USB-C 供电 |
| IMX219 摄像头 | CSI 排线 → Pi 摄像头接口 |
| USB 声卡 | 插入 Pi USB 口（`.env` 中 `AUDIO_DEVICE_ID` 选此设备） |
| 麦克风 | 接 USB 声卡输入 |
| 扬声器 | 红黑线 → USB 声卡 / 功放输出 |
| 舞台架 | 3D打印，如喜欢我的这种，可以联系我购买建模文件，微信：alex_198888 |


台上摄像头另用短线引至台前支架（见 `stage-empty.png` 实物图），与底座内 Pi 通过 CSI 排线相连。

**软件 / 账号**

| 用途 | 需要什么 | 要不要 API |
|------|----------|------------|
| 导出模型 | 开发机装 `torch`，跑 `export_dinov2_onnx.py` | 否 |
| 注册手办 | `register_feature.py` | 否 |
| 识别手办 | `stage_feature.py` 视觉部分 | 否 |
| 语音对话 | `stage_feature.py` + `.env` 里 `DOUBAO_*` | **是** |

语音 API：在 [火山引擎](https://www.volcengine.com/) 注册 → [语音应用管理](https://console.volcengine.com/speech/app) 创建应用 → 开通 **端到端实时语音** → 把 App ID、Access Key、App Key 填入 `.env`。  
文档：[Realtime API](https://www.volcengine.com/docs/6561/1801940)。按量计费，密钥只放本地 `.env`，勿提交 git。

---

## 部署步骤

### 1. 克隆并安装依赖

```bash
git clone https://github.com/dww1999zj-cn/figure-stage.git
cd figure-stage
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 导出 DINOv2 模型（可使用开发电脑环境做，导出到树莓派调用）

```bash
pip install torch
python scripts/export_dinov2_onnx.py
```

把生成的 `models/dinov2_vits14.onnx` 拷到 Pi（如 `~/Desktop/`）。

### 3. 配置 `.env`

```bash
cp .env.example .env
```

至少填写：

- `FEATURE_MODEL_PATH` — Pi 上 ONNX 路径  
- `DOUBAO_APP_ID` / `DOUBAO_ACCESS_KEY` / `DOUBAO_APP_KEY` — 语音三项（要对话时必填）  
- `AUDIO_DEVICE_ID` — USB 声卡编号（`python -c "import sounddevice; print(sounddevice.query_devices())"` 查看）

### 4. 注册手办

每只手办注册一次。机位、光线与以后运行时尽量相同。
**重要：** 小白、小黄是代码示例。注册前请先改 `feature_embed.py`、`stage_feature.py` 中的 `VALID_TARGET_KEYS` 与 `CHARACTER_CONFIG`，再执行 `register_feature.py`。

```bash
python register_feature.py register --key wdog --name 小白   
python register_feature.py register --key ydog --name 小黄   
python register_feature.py list
python register_feature.py verify --key wdog
```



结果在 `registry/`（`wdog.npz` 等 + `manifest.json`）。

### 5. 运行

```bash
python stage_feature.py
```

- 启动后 **约 3 秒保持空台**（采背景）
- 手办上台 → 本地匹配 → 开始语音
- 换另一只手办 → 可开新会话

---

## 手办 key 从哪来？

能注册哪些 key，写在代码里，**不是**看 `registry/` 里有什么文件。

| 文件 | 管什么 |
|------|--------|
| `feature_embed.py` → `VALID_TARGET_KEYS` | 允许注册哪些 key |
| `stage_feature.py` → `VALID_TARGET_KEYS` | 允许识别哪些 key |
| `stage_feature.py` → `CHARACTER_CONFIG` | 每个 key 说什么话、什么音色 |
| `registry/{key}.npz` | 该 key 已采过的视觉特征 |

**新增一只手办：** 上面三个代码处都加上同一个 key → 再 `register` → 再运行。

---

## 常用命令

| 命令 | 作用 |
|------|------|
| `register_feature.py register --key wdog --name 手办A` | 注册 |
| `register_feature.py list` | 看已注册 |
| `register_feature.py verify --key wdog` | 测识别 |
| `register_feature.py delete --key wdog` | 删除 |
| `stage_feature.py` | 上台识别 + 聊天 |

---

## 项目结构

```
figure-stage/
├── assets/
│   ├── figure-stage-promo.jpg  # 宣传图
│   ├── stage-empty.jpg         # 空台实物
│   └── stage-inside.jpg        # 底座内部接线参考
├── stage_feature.py          # 主程序（推荐：树莓派本地识别 + 语音）
├── register_feature.py       # 注册
├── feature_embed.py          # 模型 + registry
├── doubao_dialog.py          # 语音会话配置
├── scripts/export_dinov2_onnx.py
├── .env.example
├── registry/                 # 注册结果（本地，不进 git）
└── models/*.onnx             # 模型文件（本地，不进 git）
```

低成本 ESP32 实验线（云端识别，非推荐方案）见独立仓库：[figure-stage-esp32](https://github.com/dww1999zj-cn/figure-stage-esp32)。

---

## 许可

| 文档 | 说明 |
|------|------|
| [LICENSE](LICENSE) | 非商业免费 |
| [COMMERCIAL.md](COMMERCIAL.md) | 商用联系 dww1999zj@gmail.com |
| [TRADEMARK.md](TRADEMARK.md) | 项目名称规则 |

许可人：**dww1999zj-cn**
