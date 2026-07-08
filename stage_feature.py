#!/usr/bin/env python3
"""
手办舞台 — DINOv2 特征匹配 + 豆包实时语音

用法:
    python scripts/export_dinov2_onnx.py   # 开发机，仅需一次
    python register_feature.py register --key ydog --name 小黄
    python stage_feature.py

启动后约 3 秒内需保持展示台为空，用于采集背景 baseline。
"""

import json
import os
import queue
import signal as pysignal
import sys
import threading
import time
import uuid

import cv2
import numpy as np
import sounddevice as sd
from scipy import signal as scipy_signal
from websockets.sync.client import connect

from doubao_dialog import build_dialog
from feature_embed import DINOv2Embedder, load_all_centroids, match_embedding

VALID_TARGET_KEYS = frozenset({"bubu", "sea", "wdog", "ydog"})


def _load_env_file() -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _require_env(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print("缺少环境变量:", ", ".join(missing), file=sys.stderr)
        print("请复制 .env.example 为 .env 并填写", file=sys.stderr)
        sys.exit(1)


_load_env_file()

# ===================== 豆包语音 =====================
DOUBAO_APP_ID = os.environ.get("DOUBAO_APP_ID", "")
DOUBAO_ACCESS_KEY = os.environ.get("DOUBAO_ACCESS_KEY", "")
DOUBAO_WS_URL = os.environ.get(
    "DOUBAO_WS_URL", "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
)
DOUBA_RESOURCE_ID = os.environ.get("DOUBAO_RESOURCE_ID", "volc.speech.dialog")
DOUBA_APP_KEY = os.environ.get("DOUBAO_APP_KEY", "")
DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "1.2.1.1")

# ===================== DINOv2 特征匹配 =====================
FEATURE_REGISTRY_DIR = os.environ.get("FEATURE_REGISTRY_DIR", "registry")
FEATURE_MODEL_PATH = os.environ.get("FEATURE_MODEL_PATH", "/home/pi/Desktop/dinov2_vits14.onnx")
FEATURE_MIN_SCORE = float(os.environ.get("FEATURE_MIN_SCORE", "0.60"))
FEATURE_MIN_MARGIN = float(os.environ.get("FEATURE_MIN_MARGIN", "0.10"))
FEATURE_ROI_FRACTION = float(os.environ.get("FEATURE_ROI_FRACTION", "0.6"))
FEATURE_MIN_INTERVAL_SEC = float(os.environ.get("FEATURE_MIN_INTERVAL_SEC", "1.0"))

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
IDLE_TIMEOUT_SECONDS = float(os.environ.get("IDLE_TIMEOUT_SECONDS", "60"))

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

audio_file_queue = queue.Queue(maxsize=500)
play_buffer = queue.Queue(maxsize=100)
switch_target_event = threading.Event()
program_exit_event = threading.Event()
playback_suppressed = threading.Event()
HAS_DISPLAY = os.environ.get("DISPLAY") is not None

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


def recognize_figure_local(
    embedder: DINOv2Embedder,
    centroids: dict[str, np.ndarray],
    frame_rgb: np.ndarray,
) -> tuple[str | None, float, float, str]:
    """DINOv2 本地匹配，返回 (target_key, score, margin, summary)。"""
    global last_feature_match_time

    with feature_match_lock:
        now = time.time()
        if now - last_feature_match_time < FEATURE_MIN_INTERVAL_SEC:
            return None, 0.0, 0.0, "rate_limited"
        last_feature_match_time = now

    if not centroids:
        return None, 0.0, 0.0, "empty_registry"

    try:
        query = embedder.embed(frame_rgb, roi_fraction=FEATURE_ROI_FRACTION)
    except Exception as e:
        print(f"[ERROR] 特征提取失败: {e}")
        return None, 0.0, 0.0, str(e)

    key, score, margin, ranked = match_embedding(
        query,
        centroids,
        min_score=FEATURE_MIN_SCORE,
        min_margin=FEATURE_MIN_MARGIN,
    )
    top = ranked[0] if ranked else ("?", 0.0)
    summary = f"top={top[0]} score={top[1]:.3f} margin={margin:.3f}"

    if key is None:
        print(f"[WARN] 特征匹配置信不足: {summary}")
        return None, score, margin, summary

    if key not in VALID_TARGET_KEYS:
        print(f"[WARN] 未知 target_key: {key}")
        return None, score, margin, summary

    return key, score, margin, summary


def frame_diff_score(frame_rgb: np.ndarray, baseline_gray: np.ndarray) -> float:
    gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    if gray.shape != baseline_gray.shape:
        gray = cv2.resize(gray, (baseline_gray.shape[1], baseline_gray.shape[0]))
    return float(np.mean(cv2.absdiff(gray, baseline_gray)))


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
    except Exception as e:
        print(f"[ERROR] 麦克风异常: {e}")


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

        def recv():
            nonlocal is_switch_exit
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
                        elif evt == EVENT_ASR_ENDED:
                            playback_suppressed.clear()
                        elif evt == EVENT_ASR_RESPONSE:
                            try:
                                text = json.loads(pay)["results"][0]["text"]
                                if text.strip():
                                    with last_active_lock:
                                        last_active_time = time.time()
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
                if elapsed > IDLE_TIMEOUT_SECONDS:
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


def _start_conversation(target_key: str):
    global last_switch_time
    last_switch_time = time.time()
    threading.Thread(
        target=doubao_ai_interaction,
        args=(CHARACTER_CONFIG[target_key], target_key),
        daemon=True,
    ).start()


def _switch_conversation(target_key: str):
    global last_switch_time
    print(f"切换到: {target_key}")
    switch_target_event.set()
    wait_start = time.time()
    while time.time() - wait_start < 2.5:
        with state_lock:
            if not is_talking:
                break
        time.sleep(0.05)
    switch_target_event.clear()
    _start_conversation(target_key)


def feature_detection_thread():
    global is_running, last_switch_time

    try:
        embedder = DINOv2Embedder(FEATURE_MODEL_PATH)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        is_running = False
        return

    centroids = load_all_centroids(FEATURE_REGISTRY_DIR)
    if not centroids:
        print(f"[ERROR] registry 为空: {FEATURE_REGISTRY_DIR}")
        print("请先运行: python register_feature.py register --key ydog --name 小黄")
        is_running = False
        return

    print(f"已加载 registry: {sorted(centroids.keys())}  model={embedder.model_path}")

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
        return

    baseline = capture_baseline(picam2)

    object_present = False
    stable = 0
    pending_key = None
    pending_stable = 0
    talk_last_key = None
    talk_rematch_stable = 0
    idle_last_key = None
    idle_rematch_stable = 0
    idle_match_summary = ""
    last_label = ""

    print("等待手办上台（本地画面变化 + DINOv2 特征匹配）...")

    while is_running:
        frame = picam2.capture_array()
        frame = cv2.flip(frame, 1)
        if frame.shape[2] == 4:
            frame = frame[..., :3]

        diff = frame_diff_score(frame, baseline)
        if object_present:
            if diff < TRIGGER_DIFF_OFF:
                object_present = False
                stable = 0
                pending_key = None
                pending_stable = 0
        else:
            if diff >= TRIGGER_DIFF_ON:
                stable += 1
            else:
                stable = max(0, stable - 1)

            if stable >= REQUIRED_STABLE_FRAMES:
                object_present = True
                stable = 0
                print(f"本地触发: diff={diff:.1f}，DINOv2 匹配...")
                key, score, margin, summary = recognize_figure_local(embedder, centroids, frame)
                if key:
                    print(f"特征确认: {key} score={score:.3f} margin={margin:.3f} ({summary})")
                    with state_lock:
                        talking = is_talking
                    with last_convo_end_lock:
                        since_end = time.time() - last_convo_end_time
                    if not talking and since_end >= IDLE_TIMEOUT_SECONDS:
                        _start_conversation(key)
                    pending_key = key
                    pending_stable = REQUIRED_STABLE_FRAMES
                else:
                    object_present = False
                    print(f"特征未确认: {summary}")

        with state_lock:
            talking = is_talking
            now_target = current_target

        # 对话中换娃：每帧特征复核（对齐 stage_yolo，不依赖 object_present / diff）
        if talking:
            if (time.time() - last_switch_time) > SWITCH_COOLDOWN_SECONDS:
                key, score, margin, summary = recognize_figure_local(embedder, centroids, frame)
                if key and key != talk_last_key:
                    talk_rematch_stable = 1
                    talk_last_key = key
                elif key and key == talk_last_key:
                    talk_rematch_stable += 1
                elif summary != "rate_limited":
                    talk_rematch_stable = max(0, talk_rematch_stable - 1)
            else:
                talk_rematch_stable = max(0, talk_rematch_stable - 1)

            if (
                talk_rematch_stable >= REQUIRED_STABLE_FRAMES
                and talk_last_key
                and talk_last_key != now_target
            ):
                key_to_switch = talk_last_key
                print(f"特征切换确认: {key_to_switch} (原 {now_target})，准备切换对话...")
                _switch_conversation(key_to_switch)
                talk_rematch_stable = 0
                talk_last_key = None
                pending_key = key_to_switch

        # 会话结束但台上仍有物体：冷却后再匹配并开新对话
        elif not talking and object_present:
            with state_lock:
                ended_target = last_ended_target
            with last_convo_end_lock:
                since_end = time.time() - last_convo_end_time
            idle_ready = since_end >= IDLE_TIMEOUT_SECONDS
            if (time.time() - last_switch_time) > SWITCH_COOLDOWN_SECONDS:
                key, score, margin, summary = recognize_figure_local(embedder, centroids, frame)
                if key and key != idle_last_key:
                    idle_rematch_stable = 1
                    idle_last_key = key
                    idle_match_summary = summary
                elif key and key == idle_last_key:
                    idle_rematch_stable += 1
                elif summary != "rate_limited":
                    idle_rematch_stable = max(0, idle_rematch_stable - 1)
            else:
                idle_rematch_stable = max(0, idle_rematch_stable - 1)

            swap_ready = idle_last_key and idle_last_key != ended_target
            if (
                idle_rematch_stable >= REQUIRED_STABLE_FRAMES
                and idle_last_key
                and (idle_ready or swap_ready)
            ):
                key_to_start = idle_last_key
                reason = "换娃" if swap_ready and not idle_ready else "空闲重启"
                print(f"{reason}后特征确认: {key_to_start} ({idle_match_summary})")
                _start_conversation(key_to_start)
                idle_rematch_stable = 0
                idle_last_key = None
                idle_match_summary = ""
                pending_key = key_to_start
        else:
            if not talking:
                talk_rematch_stable = 0
                talk_last_key = None
            if not object_present:
                idle_rematch_stable = 0
                idle_last_key = None
                idle_match_summary = ""

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

    picam2.stop()
    if HAS_DISPLAY:
        cv2.destroyAllWindows()
    is_running = False


def signal_handler(sig, frame):
    global is_running
    print("\n退出中...")
    is_running = False
    program_exit_event.set()
    play_buffer.put("EOF")
    time.sleep(0.5)
    sys.exit(0)


def main():
    _require_env("DOUBAO_APP_ID", "DOUBAO_ACCESS_KEY", "DOUBAO_APP_KEY")

    print("启动 [特征匹配] stage_feature.py")
    print("配置: .env / 环境变量")
    print("本地: 画面变化 + DINOv2  |  语音: 豆包 Realtime")
    print(f"registry={FEATURE_REGISTRY_DIR}  score>={FEATURE_MIN_SCORE}  margin>={FEATURE_MIN_MARGIN}")
    pysignal.signal(pysignal.SIGINT, signal_handler)
    pysignal.signal(pysignal.SIGTERM, signal_handler)

    threading.Thread(target=audio_play_thread, daemon=True).start()
    threading.Thread(target=record_mic_to_queue, daemon=True).start()
    threading.Thread(target=feature_detection_thread, daemon=True).start()

    print("启动完成，有桌面时按 q 退出")
    while is_running:
        time.sleep(1)


if __name__ == "__main__":
    main()
