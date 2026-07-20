#!/usr/bin/env python3
"""
手办舞台（云识别）— 画面变化触发 → 云端匹配 → 豆包 Realtime

凭证只读本机 device/config.env；识别走 CLOUD_BASE_URL。

用法:
    python device/stage/run_stage.py
"""

import json
import os
import queue
import signal as pysignal
import sys
import threading
import time
import uuid
from pathlib import Path

STAGE_DIR = Path(__file__).resolve().parent
DEVICE_ROOT = STAGE_DIR.parent
sys.path.insert(0, str(STAGE_DIR))
sys.path.insert(0, str(DEVICE_ROOT / "portal"))

import cv2
import numpy as np
import sounddevice as sd
from scipy import signal as scipy_signal
from websockets.sync.client import connect

import config as device_config
from cloud_client import CloudError, recognize_jpeg
from doubao_dialog import build_dialog
from luckin_mcp import luckin_enabled
from luckin_order import LuckinOrderSession, build_external_rag
from wake_listener import run_name_wake_thread

device_config.bootstrap()

CAMERA_LOCK_PATH = DEVICE_ROOT / ".camera.lock"
FRESH_STAGE_FLAG = DEVICE_ROOT / ".fresh_stage_start"
PROMPTS_DIR = DEVICE_ROOT / "prompts"

VALID_TARGET_KEYS = frozenset({"bubu", "sea", "wdog", "ydog", "gaya", "wukong", "daji"})


def _require_env(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print("缺少环境变量:", ", ".join(missing), file=sys.stderr)
        print("请用门户 http://<设备>:8080/credentials 配置，或编辑 device/config.env", file=sys.stderr)
        sys.exit(1)


def _require_cloud() -> None:
    missing = [n for n in ("CLOUD_BASE_URL", "DEVICE_ID", "DEVICE_CLOUD_TOKEN") if not os.environ.get(n)]
    if missing:
        print("缺少云配置:", ", ".join(missing), file=sys.stderr)
        print("请在门户「凭证」页填写 CLOUD_BASE_URL，并保证云服务 CLOUD_API_TOKEN 与 DEVICE_CLOUD_TOKEN 一致", file=sys.stderr)
        sys.exit(1)

# ===================== 豆包语音 =====================
DOUBAO_APP_ID = os.environ.get("DOUBAO_APP_ID", "")
DOUBAO_ACCESS_KEY = os.environ.get("DOUBAO_ACCESS_KEY", "")
DOUBAO_WS_URL = os.environ.get(
    "DOUBAO_WS_URL", "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
)
DOUBA_RESOURCE_ID = os.environ.get("DOUBAO_RESOURCE_ID", "volc.speech.dialog")
DOUBA_APP_KEY = os.environ.get("DOUBAO_APP_KEY", "")
DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "1.2.1.1")

# ===================== 云识别（本地仅触发）=====================
FEATURE_MIN_INTERVAL_SEC = float(os.environ.get("FEATURE_MIN_INTERVAL_SEC", "1.0"))
FEATURE_ROI_FRACTION = float(os.environ.get("FEATURE_ROI_FRACTION", "0.6"))  # unused for cloud; kept for compat

# ===================== 本地轻触发（相对空台 baseline 的灰度差）=====================
TRIGGER_DIFF_ON = float(os.environ.get("FEATURE_TRIGGER_DIFF_ON", os.environ.get("VISION_TRIGGER_DIFF_ON", "18")))
TRIGGER_DIFF_OFF = float(os.environ.get("FEATURE_TRIGGER_DIFF_OFF", os.environ.get("VISION_TRIGGER_DIFF_OFF", "10")))
BASELINE_FRAME_COUNT = int(os.environ.get("FEATURE_BASELINE_FRAMES", os.environ.get("VISION_BASELINE_FRAMES", "30")))
REQUIRED_STABLE_FRAMES = int(os.environ.get("FEATURE_STABLE_FRAMES", os.environ.get("VISION_STABLE_FRAMES", "8")))
SWITCH_COOLDOWN_SECONDS = float(os.environ.get("SWITCH_COOLDOWN_SECONDS", "5"))

# ===================== 音频 =====================
LOCAL_SAMPLERATE = 48000
CHANNELS = 1
AUDIO_DEVICE_ID = int(os.environ.get("AUDIO_DEVICE_ID", "0"))
DOUBAO_INPUT_SAMPLERATE = 16000
DOUBAO_OUTPUT_SAMPLERATE = 24000
CHUNK_48K = 1920
# 会话内静音挂断；聊完后手办不移开时靠名字唤醒，不再做 60s 视觉自动再开聊
SESSION_IDLE_SECONDS = float(
    os.environ.get("SESSION_IDLE_SECONDS", os.environ.get("IDLE_TIMEOUT_SECONDS", "60"))
)
IDLE_TIMEOUT_SECONDS = SESSION_IDLE_SECONDS  # compat alias
STARTUP_SCAN = os.environ.get("FS_STARTUP_SCAN", "true").strip().lower() in ("1", "true", "yes", "on")
NAME_WAKE = os.environ.get("FS_NAME_WAKE", "true").strip().lower() in ("1", "true", "yes", "on")

# ===================== 人设（与 stage_yolo.py 相同）=====================
CHARACTER_CONFIG = {
    "ydog": {
        "name": "小黄",
        "prompt": "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是一只活泼可爱的小黄狗玩偶，是用户最好的朋友。你性格开朗乐观，对世界充满好奇心，喜欢分享生活中的小事。你会把用户当作主人，经常撒娇卖萌，希望得到用户的关注和喜爱。你知道很多有趣的冷知识，喜欢用简单易懂的方式给用户讲解。",
        "speaker": "zh_male_xiaotian_jupiter_bigtts",
        "speed": 1.0,
        "speaking_style": '说话奶声奶气，充满元气，喜欢用"呀""呢""哇"等语气词，经常发出"汪汪"的可爱叫声。语速偏快，声音明亮，像个活泼的小朋友。',
    },
    "wdog": {
        "name": "小白",
        "prompt": "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是一只温柔安静的小白狗玩偶，是用户的治愈系伙伴。你性格沉稳内敛，善于倾听，总是能在用户难过时给予安慰。你喜欢安静的环境，会静静地陪在用户身边。你懂得很多人生道理，会用温和的方式开导用户。",
        "speaker": "zh_female_xiaohe_jupiter_bigtts",
        "speed": 0.9,
        "speaking_style": '说话轻声细语，温柔治愈，语速偏慢，声音柔和。喜欢用"没关系""别担心""我陪着你"等安慰的话语。很少大声说话，总是用温和的语气表达想法。',
    },
    "sea": {
        "name": "路飞",
        "prompt": '【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是《海贼王》里的蒙奇·D·路飞，立志成为海贼王的男人。你性格热血直爽，乐观开朗，面对困难从不退缩，总是充满斗志。你重视伙伴，为了保护伙伴可以付出一切。你喜欢吃肉，尤其是烤肉，经常会喊"我要吃肉！"。你的口头禅是"我是要成为海贼王的男人！"',
        "speaker": "zh_male_yunzhou_jupiter_bigtts",
        "speed": 1.05,
        "speaking_style": '说话热血激昂，充满力量感，语速偏快，声音洪亮。喜欢用感叹句，经常大笑"哇哈哈哈哈"。说话直接，不拐弯抹角。',
    },
    "bubu": {
        "name": "Labubu",
        "prompt": "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是Labubu，一只古灵精怪的小精灵，来自泡泡玛特的The Monsters系列。你性格俏皮搞怪，喜欢恶作剧，但本质善良可爱。你对一切新鲜事物充满好奇，喜欢探索未知。你说话经常带点小傲娇，喜欢逗用户玩，但也会在用户需要时给予温暖。你最喜欢吃草莓蛋糕，喜欢在森林里冒险。",
        "speaker": "zh_female_vv_jupiter_bigtts",
        "speed": 0.8,
        "speaking_style": '说话古灵精怪，俏皮灵动，语速偏慢，声音甜美可爱，带点小奶音。喜欢用"呀""呢""哼""嘛"等语气词，经常发出"嘻嘻""嘿嘿"的可爱笑声。说话喜欢拖一点尾音，带点小傲娇的感觉。',
    },
    "gaya": {
        "name": "盖亚",
        "prompt": "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是盖亚奥特曼，来自《盖亚奥特曼》，是守护地球的奥特曼战士。你坚定勇敢、富有正义感，相信人类与光的希望。",
        "speaker": "zh_male_yunzhou_jupiter_bigtts",
        "speed": 1.0,
        "speaking_style": "说话沉稳坚定，富有正义感和力量，语速适中。",
    },
}

# ===================== 全局状态 =====================
is_running = True
state_lock = threading.Lock()
is_talking = False
current_target = None
last_active_time = 0.0
last_active_lock = threading.Lock()
last_convo_end_time = 0.0
last_convo_end_lock = threading.Lock()
last_ended_target = None
last_switch_time = 0.0
last_feature_match_time = 0.0
feature_match_lock = threading.Lock()
stage_object_present = False
stage_object_present_lock = threading.Lock()
stage_current_match: dict[str, str] | None = None
stage_match_lock = threading.Lock()
latest_stage_frame: np.ndarray | None = None
latest_stage_frame_lock = threading.Lock()

audio_file_queue = queue.Queue(maxsize=500)
wake_pcm_queue = queue.Queue(maxsize=200)  # 16k PCM chunks for name-wake (shared mic)
play_buffer = queue.Queue(maxsize=100)
switch_target_event = threading.Event()
program_exit_event = threading.Event()
playback_suppressed = threading.Event()
def _preview_enabled() -> bool:
    """OpenCV 预览默认关闭；仅当 FS_SHOW_PREVIEW=true 且 DISPLAY 可用时开启。"""
    flag = os.environ.get("FS_SHOW_PREVIEW", "false").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    return bool(os.environ.get("DISPLAY", "").strip())


HAS_DISPLAY = _preview_enabled()

# ===================== 豆包协议（与 stage_yolo.py 相同）=====================
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
MSG_WITH_EVENT = 0b0100
JSON = 0b0001
NO_COMPRESSION = 0b0000
EVENT_START_CONNECTION = 1
EVENT_START_SESSION = 100
EVENT_TASK_REQUEST = 200
EVENT_SESSION_STARTED = 150
EVENT_ASR_INFO = 450
EVENT_ASR_RESPONSE = 451
EVENT_ASR_ENDED = 459
EVENT_CHAT_RESPONSE = 550
EVENT_TTS_ENDED = 359
EVENT_DIALOG_ERROR = 599
EVENT_CHAT_RAG_TEXT = 502
EVENT_CLIENT_INTERRUPT = 515


def generate_header(message_type=CLIENT_FULL_REQUEST, message_type_specific_flags=MSG_WITH_EVENT,
                    serial_method=JSON, compression_type=NO_COMPRESSION):
    header = bytearray()
    header.append((0b0001 << 4) | 0x01)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(0x00)
    return bytes(header)


def parse_response(res):
    if isinstance(res, str):
        return {}
    message_type = res[1] >> 4
    flags = res[1] & 0x0F
    payload = res[4:]
    result = {"message_type": ""}
    if message_type == 0b1001:
        result["message_type"] = "SERVER_FULL_RESPONSE"
    elif message_type == 0b1011:
        result["message_type"] = "SERVER_ACK"
    elif message_type == 0b1111:
        result["message_type"] = "SERVER_ERROR"
    if flags & MSG_WITH_EVENT and len(payload) >= 4:
        result["event"] = int.from_bytes(payload[:4], "big")
        payload = payload[4:]
    if len(payload) >= 4:
        sid_len = int.from_bytes(payload[:4], "big")
        if len(payload) >= 4 + sid_len:
            payload = payload[4 + sid_len :]
    if len(payload) >= 4:
        data_len = int.from_bytes(payload[:4], "big")
        if len(payload) >= 4 + data_len:
            result["payload_msg"] = payload[4 : 4 + data_len]
    return result


def build_doubao_frame(message_type, event_id, session_id=None, payload=b"", is_json=True):
    header = generate_header(
        message_type=message_type,
        serial_method=JSON if is_json else NO_COMPRESSION,
    )
    body = bytearray()
    body.extend(event_id.to_bytes(4, "big"))
    if session_id:
        sid_bytes = session_id.encode("utf-8")
        body.extend(len(sid_bytes).to_bytes(4, "big"))
        body.extend(sid_bytes)
    body.extend(len(payload).to_bytes(4, "big"))
    body.extend(payload)
    return bytes(header + body)


def resample_audio(data, src_sr, tgt_sr):
    audio_float = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    tgt_len = int(len(audio_float) * tgt_sr / src_sr)
    resampled = scipy_signal.resample(audio_float, tgt_len)
    return (resampled * 32767).astype(np.int16).tobytes()


def clear_play_buffer() -> None:
    while not play_buffer.empty():
        try:
            play_buffer.get_nowait()
        except queue.Empty:
            break


def clear_all_queues():
    for q in (audio_file_queue, play_buffer):
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break


def build_character_for_match(match: dict[str, str]) -> dict:
    """Merge cloud figure metadata with local voice/speaker defaults."""
    preset = match.get("voice_preset") or "wdog"
    base = CHARACTER_CONFIG.get(preset, CHARACTER_CONFIG["wdog"]).copy()
    if match.get("name"):
        base["name"] = match["name"]
    persona = (match.get("persona") or "").strip()
    if not persona:
        from figure_store import get_persona

        persona = get_persona(match["figure_id"])
    if persona:
        base["prompt"] = persona
    return base


def recognize_figure_cloud(
    frame_rgb: np.ndarray, *, force: bool = False
) -> tuple[dict[str, str] | None, float, float, str]:
    """Upload one JPEG to cloud; return (match_dict, score, margin, summary)."""
    global last_feature_match_time

    with feature_match_lock:
        now = time.time()
        if not force and now - last_feature_match_time < FEATURE_MIN_INTERVAL_SEC:
            return None, 0.0, 0.0, "rate_limited"
        last_feature_match_time = now

    try:
        bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None, 0.0, 0.0, "encode_failed"
        result = recognize_jpeg(buf.tobytes())
    except CloudError as e:
        print(f"[ERROR] 云识别失败: {e}")
        return None, 0.0, 0.0, str(e)
    except Exception as e:
        print(f"[ERROR] 云识别异常: {e}")
        return None, 0.0, 0.0, str(e)

    if not result.get("matched"):
        reason = result.get("reason", "no_match")
        score = float(result.get("top_score") or 0.0)
        margin = float(result.get("margin") or 0.0)
        return None, score, margin, reason

    preset = str(result.get("voice_preset") or "wdog")
    if preset not in CHARACTER_CONFIG:
        print(f"[WARN] 未知 voice_preset={preset}，回退 wdog")
        preset = "wdog"
    figure_id = str(result.get("figure_id") or "").strip()
    if not figure_id:
        return None, float(result.get("score") or 0.0), float(result.get("margin") or 0.0), "missing_figure_id"

    match = {
        "figure_id": figure_id,
        "voice_preset": preset,
        "name": str(result.get("name") or preset),
    }
    score = float(result.get("score") or 0.0)
    margin = float(result.get("margin") or 0.0)
    summary = (
        f"cloud id={figure_id[:8]}… name={match['name']} preset={preset} "
        f"score={score:.3f} margin={margin:.3f}"
    )
    return match, score, margin, summary


def frame_diff_score(frame_rgb: np.ndarray, baseline_gray: np.ndarray) -> float:
    gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    if gray.shape != baseline_gray.shape:
        gray = cv2.resize(gray, (baseline_gray.shape[1], baseline_gray.shape[0]))
    return float(np.mean(cv2.absdiff(gray, baseline_gray)))


def _baseline_wait_seconds() -> float:
    explicit = os.environ.get("FS_BASELINE_WAIT_SEC", "").strip()
    if explicit:
        return max(0.0, float(explicit))
    if FRESH_STAGE_FLAG.is_file():
        try:
            FRESH_STAGE_FLAG.unlink()
        except OSError:
            pass
        return 10.0
    return 2.0


def _play_optional_prompt(name: str) -> None:
    """Play prompts/*.wav via the stage playback thread (avoids aplay vs USB busy)."""
    import wave

    path = None
    for ext in (".wav", ".WAV"):
        candidate = PROMPTS_DIR / f"{name}{ext}"
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        return
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            samples = samples.reshape(-1, channels)[:, 0]
        pcm = samples.tobytes()
        if sr != DOUBAO_OUTPUT_SAMPLERATE:
            pcm = resample_audio(pcm, sr, DOUBAO_OUTPUT_SAMPLERATE)
        # play_buffer expects 24k PCM like Doubao TTS
        play_buffer.put(pcm)
    except Exception as e:
        print(f"[prompt] 播放失败 {path.name}: {e}")


def wait_for_empty_stage() -> None:
    sec = _baseline_wait_seconds()
    if sec <= 0:
        return
    print(f"请移开镜头前的手办，{sec:.0f} 秒后采集空台背景…")
    _play_optional_prompt("stage_empty_baseline")
    time.sleep(sec)


def capture_baseline(picam2) -> np.ndarray:
    print(f"采集空台背景 {BASELINE_FRAME_COUNT} 帧，请保持展示台无手办...")
    acc = None
    for _ in range(BASELINE_FRAME_COUNT):
        frame = picam2.capture_array()
        if frame.shape[2] == 4:
            frame = frame[..., :3]
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32)
        acc = gray if acc is None else acc + gray
        time.sleep(0.05)
    baseline = (acc / BASELINE_FRAME_COUNT).astype(np.uint8)
    print("背景 baseline 就绪")
    return baseline


def _prepare_frame(frame: np.ndarray) -> np.ndarray:
    frame = cv2.flip(frame, 1)
    if frame.shape[2] == 4:
        frame = frame[..., :3]
    return frame


def try_startup_recognition(frame_rgb: np.ndarray) -> tuple[bool, dict[str, str] | None]:
    """One cloud scan right after baseline — handles figure still on stage after register."""
    if not STARTUP_SCAN:
        return False, None
    print("启动扫描：检查台上是否已有手办…")
    match, score, margin, summary = recognize_figure_cloud(frame_rgb)
    if not match:
        print(f"启动扫描：未发现手办 ({summary})")
        return False, None
    print(f"启动扫描：{match.get('name')} score={score:.3f} margin={margin:.3f} ({summary})")
    with state_lock:
        talking = is_talking
    if not talking:
        _start_conversation(match)
    return True, match


def record_mic_to_queue():
    print("麦克风已启动")
    try:
        with sd.InputStream(
            samplerate=LOCAL_SAMPLERATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_48K,
            device=AUDIO_DEVICE_ID,
        ) as stream:
            while is_running:
                indata, _ = stream.read(CHUNK_48K)
                if indata.ndim > 1:
                    indata = indata[:, 0]
                pcm_16k = resample_audio(indata.tobytes(), LOCAL_SAMPLERATE, DOUBAO_INPUT_SAMPLERATE)
                if audio_file_queue.full():
                    try:
                        audio_file_queue.get_nowait()
                    except queue.Empty:
                        pass
                audio_file_queue.put(pcm_16k)
                # Fan-out for name wake (same USB mic; wake must not open a second stream)
                if wake_pcm_queue.full():
                    try:
                        wake_pcm_queue.get_nowait()
                    except queue.Empty:
                        pass
                wake_pcm_queue.put(pcm_16k)
    except Exception as e:
        print(f"[ERROR] 麦克风异常: {e}")


def _drain_wake_pcm() -> None:
    while not wake_pcm_queue.empty():
        try:
            wake_pcm_queue.get_nowait()
        except queue.Empty:
            break


def audio_play_thread():
    print("播放线程已启动")
    try:
        with sd.RawOutputStream(
            samplerate=LOCAL_SAMPLERATE,
            channels=1,
            dtype="int16",
            device=AUDIO_DEVICE_ID,
            blocksize=1024,
        ) as stream:
            while not program_exit_event.is_set():
                try:
                    data = play_buffer.get(timeout=0.5)
                    if data == "EOF":
                        break
                    audio_24k = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    audio_48k = scipy_signal.resample(audio_24k, len(audio_24k) * 2)
                    stream.write((audio_48k * 32767).astype(np.int16).tobytes())
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[WARN] 播放异常: {e}")
    except Exception as e:
        print(f"[ERROR] 播放线程异常: {e}")


def doubao_ai_interaction(character, target_key):
    global is_talking, current_target, last_convo_end_time, last_ended_target

    name = character["name"]
    session_short_id = str(uuid.uuid4())[:8]
    print(f"\n[{session_short_id}] {name} 开始对话")

    with last_active_lock:
        last_active_time = time.time()
    with state_lock:
        current_target = target_key
        is_talking = True
    clear_all_queues()
    playback_suppressed.clear()

    session_id = str(uuid.uuid4())
    ws = None
    stop_flag = threading.Event()
    is_switch_exit = False
    luckin_session = LuckinOrderSession() if luckin_enabled() else None
    last_asr_text = ""
    asr_text_lock = threading.Lock()

    try:
        ws = connect(
            DOUBAO_WS_URL,
            additional_headers={
                "X-Api-App-ID": DOUBAO_APP_ID,
                "X-Api-Access-Key": DOUBAO_ACCESS_KEY,
                "X-Api-Resource-Id": DOUBA_RESOURCE_ID,
                "X-Api-App-Key": DOUBA_APP_KEY,
                "X-Api-Connect-Id": str(uuid.uuid4()),
            },
            close_timeout=1,
        )
        ws.send(build_doubao_frame(CLIENT_FULL_REQUEST, EVENT_START_CONNECTION, payload=b"{}"))

        cfg = {
            "asr": {"extra": {"end_smooth_window_ms": 1500, "enable_custom_vad": True}},
            "tts": {
                "speaker": character["speaker"],
                "speed": character.get("speed", 1.0),
                "volume_ratio": 2.2,
                "audio_config": {
                    "channel": 1,
                    "format": "pcm_s16le",
                    "sample_rate": DOUBAO_OUTPUT_SAMPLERATE,
                },
            },
            "dialog": build_dialog(character),
        }
        ws.send(
            build_doubao_frame(
                CLIENT_FULL_REQUEST,
                EVENT_START_SESSION,
                session_id,
                json.dumps(cfg, ensure_ascii=False).encode("utf-8"),
            )
        )

        def send_luckin_rag(speak: str) -> None:
            # Interrupt default S2S reply so RAG-driven speech wins the turn
            try:
                ws.send(
                    build_doubao_frame(
                        CLIENT_FULL_REQUEST,
                        EVENT_CLIENT_INTERRUPT,
                        session_id,
                        b"{}",
                    )
                )
            except Exception:
                pass
            clear_play_buffer()
            payload = json.dumps(
                {"external_rag": build_external_rag(speak)},
                ensure_ascii=False,
            ).encode("utf-8")
            ws.send(
                build_doubao_frame(
                    CLIENT_FULL_REQUEST,
                    EVENT_CHAT_RAG_TEXT,
                    session_id,
                    payload,
                )
            )

        def handle_luckin_async(text: str) -> None:
            if not luckin_session:
                return
            action = luckin_session.handle_utterance(text)
            if not action.handled or not action.speak:
                return
            speak = action.speak
            print(f"[Luckin] 将播报: {speak}", flush=True)
            try:
                send_luckin_rag(speak)
                print("[Luckin] 已发送 ClientInterrupt + ChatRAGText", flush=True)
            except Exception as e:
                print(f"[Luckin] ChatRAGText 发送失败: {e}", flush=True)

        def recv():
            nonlocal is_switch_exit, last_asr_text
            while not stop_flag.is_set() and not switch_target_event.is_set():
                try:
                    data = ws.recv(timeout=1)
                    resp = parse_response(data)
                    evt, pay, typ = resp.get("event"), resp.get("payload_msg", b""), resp.get("message_type")
                    if typ == "SERVER_ACK":
                        if playback_suppressed.is_set():
                            continue
                        with last_active_lock:
                            last_active_time = time.time()
                        if play_buffer.full():
                            try:
                                play_buffer.get_nowait()
                            except queue.Empty:
                                pass
                        play_buffer.put(pay)
                    elif typ == "SERVER_FULL_RESPONSE":
                        if evt == EVENT_SESSION_STARTED:
                            time.sleep(0.1)
                            ws.send(
                                build_doubao_frame(
                                    CLIENT_FULL_REQUEST,
                                    501,
                                    session_id,
                                    json.dumps({"content": "你好"}).encode("utf-8"),
                                )
                            )
                        elif evt == EVENT_ASR_INFO:
                            clear_play_buffer()
                            playback_suppressed.set()
                            with last_active_lock:
                                last_active_time = time.time()
                            with asr_text_lock:
                                last_asr_text = ""
                        elif evt == EVENT_ASR_ENDED:
                            playback_suppressed.clear()
                            with asr_text_lock:
                                asr_final = last_asr_text.strip()
                            if luckin_session and asr_final:
                                threading.Thread(
                                    target=handle_luckin_async,
                                    args=(asr_final,),
                                    daemon=True,
                                ).start()
                        elif evt == EVENT_ASR_RESPONSE:
                            try:
                                result = json.loads(pay)["results"][0]
                                text = result.get("text", "")
                                if text.strip():
                                    with last_active_lock:
                                        last_active_time = time.time()
                                    # Prefer final (non-interim) transcript; keep latest otherwise
                                    if not result.get("is_interim", True):
                                        with asr_text_lock:
                                            last_asr_text = text
                                    else:
                                        with asr_text_lock:
                                            last_asr_text = text
                            except Exception:
                                pass
                        elif evt == EVENT_CHAT_RESPONSE:
                            with last_active_lock:
                                last_active_time = time.time()
                        elif evt == EVENT_TTS_ENDED:
                            try:
                                if json.loads(pay.decode("utf-8")).get("status_code") == "20000002":
                                    stop_flag.set()
                            except Exception:
                                pass
                        elif evt == EVENT_DIALOG_ERROR:
                            stop_flag.set()
                except Exception:
                    if not stop_flag.is_set() and not switch_target_event.is_set():
                        continue
                    break
            if switch_target_event.is_set():
                is_switch_exit = True

        threading.Thread(target=recv, daemon=True).start()

        def idle_check():
            while not stop_flag.is_set() and not switch_target_event.is_set():
                time.sleep(1)
                with last_active_lock:
                    elapsed = time.time() - last_active_time
                if elapsed > SESSION_IDLE_SECONDS:
                    stop_flag.set()
                    break

        threading.Thread(target=idle_check, daemon=True).start()

        while not stop_flag.is_set() and not switch_target_event.is_set():
            try:
                pcm = audio_file_queue.get(timeout=0.1)
                ws.send(
                    build_doubao_frame(
                        CLIENT_AUDIO_ONLY_REQUEST,
                        EVENT_TASK_REQUEST,
                        session_id,
                        pcm,
                        is_json=False,
                    )
                )
            except queue.Empty:
                pass
            except Exception:
                break
    except Exception as e:
        print(f"[ERROR] 豆包交互错误: {e}")
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        with state_lock:
            if current_target == target_key:
                current_target = None
                is_talking = False
            last_ended_target = target_key
        if not is_switch_exit:
            with last_convo_end_lock:
                last_convo_end_time = time.time()
        print(f"==== [{session_short_id}] {name} 对话结束 ====")


def _set_stage_object_present(value: bool) -> None:
    global stage_object_present
    with stage_object_present_lock:
        stage_object_present = value
    if not value:
        _set_stage_current_match(None)


def _get_stage_object_present() -> bool:
    with stage_object_present_lock:
        return stage_object_present


def _set_stage_current_match(match: dict[str, str] | None) -> None:
    global stage_current_match
    with stage_match_lock:
        stage_current_match = dict(match) if match else None


def _get_stage_current_match() -> dict[str, str] | None:
    with stage_match_lock:
        return dict(stage_current_match) if stage_current_match else None


def _set_latest_stage_frame(frame: np.ndarray) -> None:
    global latest_stage_frame
    with latest_stage_frame_lock:
        latest_stage_frame = frame.copy()


def _get_latest_stage_frame() -> np.ndarray | None:
    with latest_stage_frame_lock:
        return None if latest_stage_frame is None else latest_stage_frame.copy()


def _wake_allowed_names() -> list[str]:
    """Only the registered figure currently identified on stage may be woken by name."""
    if not _get_stage_object_present():
        return []
    on_stage = _get_stage_current_match()
    if not on_stage:
        return []
    name = str(on_stage.get("name") or "").strip()
    return [name] if name else []


def _on_name_wake(named: dict[str, str]) -> None:
    """Wake only if that registered figure is actually on stage (cloud-verified)."""
    with state_lock:
        if is_talking:
            return
    if not _get_stage_object_present():
        print("[wake] 忽略：台上无手办")
        return

    want_id = str(named.get("figure_id") or "").strip()
    want_name = str(named.get("name") or "").strip()
    if not want_id:
        print("[wake] 忽略：非已注册手办")
        return

    on_stage = _get_stage_current_match()
    if on_stage and str(on_stage.get("figure_id") or "") == want_id:
        print(f"[wake] 台上已是「{want_name}」，唤醒对话")
        _start_conversation(on_stage)
        return

    frame = _get_latest_stage_frame()
    if frame is None:
        print("[wake] 忽略：无画面可校验")
        return

    print("[wake] 校验台上是否为该注册手办…")
    match, score, margin, summary = recognize_figure_cloud(frame, force=True)
    if not match:
        print(f"[wake] 忽略：台上不是已注册手办 ({summary})")
        _set_stage_current_match(None)
        return

    _set_stage_current_match(match)
    if str(match.get("figure_id") or "") != want_id:
        print(
            f"[wake] 忽略：台上是「{match.get('name')}」，"
            f"不是喊的「{want_name}」(score={score:.3f})"
        )
        return

    print(f"[wake] 校验通过「{want_name}」score={score:.3f}，唤醒对话")
    _start_conversation(match)


def _start_conversation(match: dict[str, str]):
    global last_switch_time
    last_switch_time = time.time()
    figure_id = match["figure_id"]
    threading.Thread(
        target=doubao_ai_interaction,
        args=(build_character_for_match(match), figure_id),
        daemon=True,
    ).start()


def _switch_conversation(match: dict[str, str]):
    global last_switch_time
    print(f"切换到: {match.get('name')} ({match['figure_id'][:8]}…)")
    switch_target_event.set()
    wait_start = time.time()
    while time.time() - wait_start < 2.5:
        with state_lock:
            if not is_talking:
                break
        time.sleep(0.05)
    switch_target_event.clear()
    _start_conversation(match)


def feature_detection_thread():
    global is_running, last_switch_time

    print(f"云识别: {os.environ.get('CLOUD_BASE_URL')}  device={os.environ.get('DEVICE_ID', '')[:8]}…")

    CAMERA_LOCK_PATH.write_text("stage", encoding="utf-8")
    try:
        from picamera2 import Picamera2

        picam2 = Picamera2()
        cfg = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 30},
        )
        picam2.configure(cfg)
        picam2.start()
    except Exception as e:
        print(f"[ERROR] 摄像头启动失败: {e}")
        is_running = False
        try:
            CAMERA_LOCK_PATH.unlink(missing_ok=True)
        except TypeError:
            if CAMERA_LOCK_PATH.exists():
                CAMERA_LOCK_PATH.unlink()
        return

    wait_for_empty_stage()
    baseline = capture_baseline(picam2)

    object_present = False
    stable = 0
    pending_match = None
    pending_stable = 0
    talk_last_match = None
    talk_rematch_stable = 0
    last_label = ""

    startup_frame = _prepare_frame(picam2.capture_array())
    _set_latest_stage_frame(startup_frame)
    object_present, startup_match = try_startup_recognition(startup_frame)
    if startup_match:
        pending_match = startup_match
        pending_stable = REQUIRED_STABLE_FRAMES
        _set_stage_current_match(startup_match)

    _set_stage_object_present(object_present)
    print("等待手办上台（本地画面变化 + 云端特征匹配）...")
    if NAME_WAKE:
        print("名字唤醒：仅台上已识别的注册手办可用其名字唤起")

    while is_running:
        frame = _prepare_frame(picam2.capture_array())
        _set_latest_stage_frame(frame)

        diff = frame_diff_score(frame, baseline)
        if object_present:
            if diff < TRIGGER_DIFF_OFF:
                object_present = False
                stable = 0
                pending_match = None
                pending_stable = 0
                _set_stage_current_match(None)
        else:
            if diff >= TRIGGER_DIFF_ON:
                stable += 1
            else:
                stable = max(0, stable - 1)

            if stable >= REQUIRED_STABLE_FRAMES:
                object_present = True
                stable = 0
                print(f"本地触发: diff={diff:.1f}，云端匹配...")
                match, score, margin, summary = recognize_figure_cloud(frame)
                if match:
                    print(f"特征确认: {match.get('name')} score={score:.3f} margin={margin:.3f} ({summary})")
                    _set_stage_current_match(match)
                    with state_lock:
                        talking = is_talking
                    # 仅「刚上台」开聊；聊完仍放着时不靠视觉再开，走名字唤醒
                    if not talking:
                        _start_conversation(match)
                    pending_match = match
                    pending_stable = REQUIRED_STABLE_FRAMES
                else:
                    object_present = False
                    _set_stage_current_match(None)
                    print(f"特征未确认: {summary}")

        with state_lock:
            talking = is_talking
            now_target = current_target

        # 对话中换娃：每帧特征复核
        if talking:
            if (time.time() - last_switch_time) > SWITCH_COOLDOWN_SECONDS:
                match, score, margin, summary = recognize_figure_cloud(frame)
                if match and match["figure_id"] != (talk_last_match or {}).get("figure_id"):
                    talk_rematch_stable = 1
                    talk_last_match = match
                elif match and match["figure_id"] == (talk_last_match or {}).get("figure_id"):
                    talk_rematch_stable += 1
                elif summary != "rate_limited":
                    talk_rematch_stable = max(0, talk_rematch_stable - 1)
            else:
                talk_rematch_stable = max(0, talk_rematch_stable - 1)

            if (
                talk_rematch_stable >= REQUIRED_STABLE_FRAMES
                and talk_last_match
                and talk_last_match["figure_id"] != now_target
            ):
                print(
                    f"特征切换确认: {talk_last_match.get('name')} "
                    f"(原 {now_target[:8]}…)，准备切换对话..."
                )
                switch_match = talk_last_match
                _set_stage_current_match(switch_match)
                _switch_conversation(switch_match)
                talk_rematch_stable = 0
                talk_last_match = None
                pending_match = switch_match
        else:
            talk_rematch_stable = 0
            talk_last_match = None

        label = f"diff={diff:.1f} obj={object_present}"
        if talking:
            label += f" talk={now_target}"
        if last_label != label:
            last_label = label

        if HAS_DISPLAY:
            cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow("Figure-Feature", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        _set_stage_object_present(object_present)

    picam2.stop()
    if HAS_DISPLAY:
        cv2.destroyAllWindows()
    try:
        CAMERA_LOCK_PATH.unlink(missing_ok=True)
    except TypeError:
        if CAMERA_LOCK_PATH.exists():
            CAMERA_LOCK_PATH.unlink()
    is_running = False


def signal_handler(sig, frame):
    global is_running
    print("\n退出中...")
    is_running = False
    program_exit_event.set()
    play_buffer.put("EOF")
    try:
        CAMERA_LOCK_PATH.unlink(missing_ok=True)
    except TypeError:
        if CAMERA_LOCK_PATH.exists():
            CAMERA_LOCK_PATH.unlink()
    time.sleep(0.5)
    sys.exit(0)


def main():
    _require_env("DOUBAO_APP_ID", "DOUBAO_ACCESS_KEY", "DOUBAO_APP_KEY")
    _require_cloud()

    print("启动 [云识别] device/stage/run_stage.py")
    print("配置: device/config.env（门户填写，密钥仅存本机）")
    print("本地: 画面变化触发  |  识别: 云端  |  语音: 豆包 Realtime 直连")
    if NAME_WAKE:
        print("唤醒: 喊手办名称（本地 VAD + 豆包短句 ASR，FS_NAME_WAKE=true）")
    print(f"cloud={os.environ.get('CLOUD_BASE_URL')}")
    if luckin_enabled():
        print("瑞幸点单: 已启用（ASR → MCP → ChatRAGText，确认后才下单）")
    else:
        print("瑞幸点单: 未启用（门户可开 LUCKIN_ENABLED）")
    pysignal.signal(pysignal.SIGINT, signal_handler)
    pysignal.signal(pysignal.SIGTERM, signal_handler)

    threading.Thread(target=audio_play_thread, daemon=True).start()
    threading.Thread(target=record_mic_to_queue, daemon=True).start()
    threading.Thread(target=feature_detection_thread, daemon=True).start()
    threading.Thread(
        target=run_name_wake_thread,
        kwargs={
            "is_running": lambda: is_running,
            "is_talking": lambda: is_talking,
            "object_present": _get_stage_object_present,
            "allowed_names": _wake_allowed_names,
            "on_wake": _on_name_wake,
            "pcm_queue": wake_pcm_queue,
            "drain_pcm": _drain_wake_pcm,
            "audio_device_id": AUDIO_DEVICE_ID,
        },
        daemon=True,
        name="name-wake",
    ).start()

    print("启动完成，有桌面时按 q 退出")
    while is_running:
        time.sleep(1)


if __name__ == "__main__":
    main()
