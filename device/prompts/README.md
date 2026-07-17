# 语音提示（自备 WAV，不进 git）

监督进程 / 舞台会在下列阶段播放（文件存在才播）：

| 文件名 | 何时 | 次数 |
|--------|------|------|
| `network_offline.wav` | 无 Wi‑Fi、进热点配网 | **同一次离线只播 1 次** |
| `network_connected_register.wav` | 已联网、凭证齐、尚未注册手办 | 该阶段一次 |
| `stage_ready.wav` | （可选）即将自动启动舞台 | 该阶段一次 |
| `stage_empty_baseline.wav` | （可选）采空台背景前 | 每次 stage 启动等待时 |

格式：建议 16-bit PCM WAV。播放：`aplay`（监督提示）或舞台播放线程（空台提示，避免与 USB 声卡冲突）。

文案建议：

- offline：连热点 `FigureStage-Setup`，打开 `10.42.0.1:8080` 配网  
- register：改连家里 Wi‑Fi 后用 `figure-stage.local:8080`  
- empty_baseline：请移开手办，建立空台  

环境变量：`FS_PROMPTS_DIR`、`FS_PROMPT_COOLDOWN_SEC`（默认 45s 播放器防抖）。
