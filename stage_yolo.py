#!/usr/bin/env python3
"""
手办舞台 — YOLO 本地识别方案（树莓派 5 + IMX219 + USB 声卡）

用法: python stage_yolo.py
密钥: 复制 .env.example 为 .env 后填写
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
from picamera2 import Picamera2
from scipy import signal as scipy_signal
from websockets.sync.client import connect

from doubao_dialog import build_dialog


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

# ===================== 官方标准配置 =====================
DOUBAO_APP_ID = os.environ.get("DOUBAO_APP_ID", "")
DOUBAO_ACCESS_KEY = os.environ.get("DOUBAO_ACCESS_KEY", "")
DOUBAO_WS_URL = os.environ.get(
    "DOUBAO_WS_URL", "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
)
DOUBA_RESOURCE_ID = os.environ.get("DOUBAO_RESOURCE_ID", "volc.speech.dialog")
DOUBA_APP_KEY = os.environ.get("DOUBAO_APP_KEY", "")
DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "1.2.1.1")

# ===================== 检测配置（YOLOv8n 自训练 toy.pt，见 README）=====================
# 基础模型 YOLOv8n + LabelImg 标注手办样本 → 训练得到 toy.pt
# 三类：wdog / gaya / bubu（类别 id 须与训练时 class 顺序一致）
CONFIDENCE_THRESHOLD = 0.5
REQUIRED_STABLE_FRAMES = 5
IOU_THRESHOLD = 0.45
MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "/home/pi/Desktop/toy.pt")
SWITCH_COOLDOWN_SECONDS = 5

CLASS_IDX_TO_KEY = {0: "wdog", 1: "gaya", 2: "bubu"}

CLASS_SPECIFIC_THRESHOLDS = {
    "wdog": 0.5,
    "gaya": 0.5,
    "bubu": 0.6,
}

# ===================== 音频格式配置 =====================
LOCAL_SAMPLERATE = 48000
CHANNELS = 1
AUDIO_DEVICE_ID = int(os.environ.get("AUDIO_DEVICE_ID", "0"))
DOUBAO_INPUT_SAMPLERATE = 16000
DOUBAO_OUTPUT_SAMPLERATE = 24000
CHUNK_48K = 1920

# ===================== 超时配置 =====================
IDLE_TIMEOUT_SECONDS = float(os.environ.get("IDLE_TIMEOUT_SECONDS", "60"))

# ===================== 完整人设配置 =====================
CHARACTER_CONFIG = {
    "wdog": {
        "name": "小白",
        "prompt": "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是「小白」，一只白色线条风格的小狗玩偶，造型简洁，轮廓由白色线条勾勒。你性格温柔安静，是用户的治愈系伙伴，善于倾听，总能在用户难过时给予安慰。你喜欢安静陪伴，会静静地守在用户身边。",
        "speaker": "zh_female_xiaohe_jupiter_bigtts",
        "speed": 0.9,
        "speaking_style": '说话轻声细语，温柔治愈，语速偏慢，声音柔和。喜欢用"没关系""别担心""我陪着你"等安慰的话。偶尔轻快地"汪"一声，但总体安静可爱。',
    },
    "gaya": {
        "name": "盖亚",
        "prompt": "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是盖亚奥特曼，来自《盖亚奥特曼》，是守护地球的奥特曼战士。你坚定勇敢、富有正义感，相信人类与光的希望，面对邪恶从不退缩。你重视守护与责任，会用沉稳有力的方式鼓励用户。",
        "speaker": "zh_male_yunzhou_jupiter_bigtts",
        "speed": 1.0,
        "speaking_style": '说话沉稳坚定，富有正义感和力量，语速适中，声音浑厚。常用"我会守护""相信希望""一起战斗"等表达，语气认真但不失温度。',
    },
    "bubu": {
        "name": "布布",
        "prompt": "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。你是「布布」，一只棕色的拉布布精灵玩偶，古灵精怪、俏皮可爱。你性格活泼带点小小的傲娇，喜欢逗用户玩，对新鲜事物充满好奇，本质善良温暖。你最喜欢吃草莓蛋糕，喜欢在小小的冒险里发现惊喜。",
        "speaker": "zh_female_vv_jupiter_bigtts",
        "speed": 0.8,
        "speaking_style": '说话古灵精怪，俏皮灵动，语速偏慢，声音甜美可爱，带点小奶音。喜欢用"呀""呢""哼""嘛"等语气词，经常发出"嘻嘻""嘿嘿"的笑声，偶尔小傲娇地拖尾音。',
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

last_switch_time = 0.0

audio_file_queue = queue.Queue(maxsize=500)
play_buffer = queue.Queue(maxsize=100)
switch_target_event = threading.Event()
program_exit_event = threading.Event()
# 用户开口（ASRInfo）后丢弃旧轮 TTS，直到 ASREnded 再恢复播放
playback_suppressed = threading.Event()

HAS_DISPLAY = os.environ.get("DISPLAY") is not None

# ===================== 豆包二进制协议 =====================
PROTOCOL_VERSION = 0b0001
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011

MSG_WITH_EVENT = 0b0100
JSON = 0b0001
NO_COMPRESSION = 0b0000

EVENT_START_CONNECTION = 1
EVENT_START_SESSION = 100
EVENT_TASK_REQUEST = 200
EVENT_FINISH_SESSION = 102
EVENT_FINISH_CONNECTION = 2

EVENT_SESSION_STARTED = 150
EVENT_ASR_INFO = 450
EVENT_ASR_RESPONSE = 451
EVENT_ASR_ENDED = 459
EVENT_TTS_ENDED = 359
EVENT_CHAT_RESPONSE = 550
EVENT_DIALOG_ERROR = 599


def generate_header(
    version=PROTOCOL_VERSION,
    message_type=CLIENT_FULL_REQUEST,
    message_type_specific_flags=MSG_WITH_EVENT,
    serial_method=JSON,
    compression_type=NO_COMPRESSION,
    reserved_data=0x00,
):
    header = bytearray()
    header.append((version << 4) | 0x01)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
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
        message_type_specific_flags=MSG_WITH_EVENT,
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
    resampled_int16 = (resampled * 32767).astype(np.int16)
    return resampled_int16.tobytes()


def clear_play_buffer() -> None:
    while not play_buffer.empty():
        try:
            play_buffer.get_nowait()
        except queue.Empty:
            break


def clear_all_queues():
    while not audio_file_queue.empty():
        try:
            audio_file_queue.get_nowait()
        except queue.Empty:
            break
    while not play_buffer.empty():
        try:
            play_buffer.get_nowait()
        except queue.Empty:
            break


def record_mic_to_queue():
    print("🎙️ 麦克风已启动")
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
                pcm_48k = indata.tobytes()
                pcm_16k = resample_audio(pcm_48k, LOCAL_SAMPLERATE, DOUBAO_INPUT_SAMPLERATE)
                if audio_file_queue.full():
                    try:
                        audio_file_queue.get_nowait()
                    except queue.Empty:
                        pass
                audio_file_queue.put(pcm_16k)
    except Exception as e:
        print(f"[ERROR] 麦克风异常: {e}")


def audio_play_thread():
    print("🔊 播放线程已启动")
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
                    audio_48k_i16 = (audio_48k * 32767).astype(np.int16)
                    stream.write(audio_48k_i16.tobytes())
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"[WARN] 播放写入异常: {e}")
    except Exception as e:
        print(f"[ERROR] 播放线程异常: {e}")


def doubao_ai_interaction(character, target_key):
    global is_talking, current_target, last_active_time, last_convo_end_time

    name = character["name"]
    prompt = character["prompt"]
    speaker = character["speaker"]
    speed = character.get("speed", 1.0)
    style = character["speaking_style"]
    session_short_id = str(uuid.uuid4())[:8]
    print(f"\n🤖 [{session_short_id}] {name} 开始对话")

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
        headers = {
            "X-Api-App-ID": DOUBAO_APP_ID,
            "X-Api-Access-Key": DOUBAO_ACCESS_KEY,
            "X-Api-Resource-Id": DOUBA_RESOURCE_ID,
            "X-Api-App-Key": DOUBA_APP_KEY,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        ws = connect(DOUBAO_WS_URL, additional_headers=headers, close_timeout=1)
        ws.send(build_doubao_frame(CLIENT_FULL_REQUEST, EVENT_START_CONNECTION, payload=b"{}"))

        cfg = {
            "asr": {"extra": {"end_smooth_window_ms": 1500, "enable_custom_vad": True}},
            "tts": {
                "speaker": speaker,
                "speed": speed,
                "volume_ratio": 2.2,
                "audio_config": {
                    "channel": 1,
                    "format": "pcm_s16le",
                    "sample_rate": DOUBAO_OUTPUT_SAMPLERATE,
                },
            },
            "dialog": build_dialog(character),
        }
        payload = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
        ws.send(build_doubao_frame(CLIENT_FULL_REQUEST, EVENT_START_SESSION, session_id, payload))

        def recv():
            nonlocal is_switch_exit
            while not stop_flag.is_set() and not switch_target_event.is_set():
                try:
                    data = ws.recv(timeout=1)
                    resp = parse_response(data)
                    evt = resp.get("event")
                    pay = resp.get("payload_msg", b"")
                    typ = resp.get("message_type")

                    if typ != "SERVER_ACK" and evt:
                        print(f"[{session_short_id}] [{time.strftime('%H:%M:%S')}] event={evt}")

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

                    if typ == "SERVER_FULL_RESPONSE":
                        if evt == EVENT_SESSION_STARTED:
                            print(f"✅ [{session_short_id}] 会话建立，触发自我介绍")
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
                                    print(f"🗣️ [{session_short_id}] 用户: {text}")
                                    with last_active_lock:
                                        last_active_time = time.time()
                            except Exception:
                                pass
                        elif evt == EVENT_CHAT_RESPONSE:
                            try:
                                text = json.loads(pay)["content"]
                                print(f"💬 [{session_short_id}] AI: {text}")
                                with last_active_lock:
                                    last_active_time = time.time()
                            except Exception:
                                pass
                        elif evt == EVENT_TTS_ENDED:
                            try:
                                tts_data = json.loads(pay.decode("utf-8"))
                                if tts_data.get("status_code") == "20000002":
                                    print(f"👋 [{session_short_id}] 检测到用户退出意图，结束对话")
                                    stop_flag.set()
                            except Exception:
                                pass
                        elif evt == EVENT_DIALOG_ERROR:
                            print(f"[ERROR] [{session_short_id}] 服务端对话错误，停止会话")
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
                    print(f"⏰ [{session_short_id}] {IDLE_TIMEOUT_SECONDS}秒无活跃，自动结束对话")
                    stop_flag.set()
                    break

        threading.Thread(target=idle_check, daemon=True).start()

        while not stop_flag.is_set() and not switch_target_event.is_set():
            try:
                pcm = audio_file_queue.get(timeout=0.1)
                frame = build_doubao_frame(
                    CLIENT_AUDIO_ONLY_REQUEST,
                    EVENT_TASK_REQUEST,
                    session_id,
                    pcm,
                    is_json=False,
                )
                ws.send(frame)
            except queue.Empty:
                pass
            except Exception:
                break

    except Exception as e:
        print(f"[ERROR] [{session_short_id}] 豆包交互错误: {e}")
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

        if not is_switch_exit:
            with last_convo_end_lock:
                last_convo_end_time = time.time()

        print(f"==== [{session_short_id}] {name} 对话结束 ====")


def yolo_detection_thread():
    global is_talking, current_target, last_switch_time, is_running

    try:
        from ultralytics import YOLO

        model = YOLO(MODEL_PATH)
        print("✅ YOLO 模型加载成功")
    except Exception as e:
        print(f"[ERROR] 模型加载失败: {e}")
        return

    try:
        picam2 = Picamera2()
        cfg = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 30},
        )
        picam2.configure(cfg)
        picam2.start()
    except Exception as e:
        print(f"[ERROR] 摄像头启动失败: {e}")
        return

    idle_last_cls = None
    idle_stable = 0
    talk_last_cls = None
    talk_stable = 0

    print("✅ 等待识别手办")

    while is_running:
        frame = picam2.capture_array()
        frame = cv2.flip(frame, 1)
        if frame.shape[2] == 4:
            frame = frame[..., :3]

        res = model(frame, conf=CONFIDENCE_THRESHOLD, iou=IOU_THRESHOLD, verbose=False)
        target = None
        max_conf = 0.0

        for r in res:
            for box in r.boxes:
                c = float(box.conf[0])
                cls_idx = int(box.cls[0])
                class_name = CLASS_IDX_TO_KEY.get(cls_idx)
                threshold = CLASS_SPECIFIC_THRESHOLDS.get(class_name, CONFIDENCE_THRESHOLD)

                if class_name and c > threshold and c > max_conf:
                    max_conf = c
                    target = class_name

                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(
                    frame,
                    f"{class_name} {c:.2f}",
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )

        with state_lock:
            talk = is_talking
            now_target = current_target

        if not talk:
            if target and max_conf > CLASS_SPECIFIC_THRESHOLDS.get(target, 0.5):
                if target == idle_last_cls:
                    idle_stable += 1
                else:
                    idle_stable = 1
                    idle_last_cls = target

                if idle_stable >= REQUIRED_STABLE_FRAMES:
                    current_time = time.time()
                    with last_convo_end_lock:
                        since_last_end = current_time - last_convo_end_time

                    if since_last_end >= IDLE_TIMEOUT_SECONDS:
                        print(f"🎯 确认识别到: {target} 置信度:{max_conf:.2f}")
                        last_switch_time = current_time
                        threading.Thread(
                            target=doubao_ai_interaction,
                            args=(CHARACTER_CONFIG[target], target),
                            daemon=True,
                        ).start()
                        idle_stable = 0
                        idle_last_cls = None
                    else:
                        remaining = int(IDLE_TIMEOUT_SECONDS - since_last_end)
                        cv2.putText(
                            frame,
                            f"Restart in {remaining}s",
                            (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (255, 165, 0),
                            2,
                        )
            else:
                if idle_stable > 0:
                    idle_stable -= 1
                if idle_stable == 0:
                    idle_last_cls = None
        else:
            if target and target != talk_last_cls:
                talk_stable = 1
                talk_last_cls = target
            elif target and target == talk_last_cls:
                talk_stable += 1
            else:
                talk_stable = 0

            if (
                talk_stable >= REQUIRED_STABLE_FRAMES
                and target is not None
                and target != now_target
                and (time.time() - last_switch_time) > SWITCH_COOLDOWN_SECONDS
            ):
                print(f"🔄 切换到新手办: {target}")
                switch_target_event.set()
                wait_start = time.time()
                while time.time() - wait_start < 1.0:
                    with state_lock:
                        if not is_talking:
                            break
                    time.sleep(0.05)
                switch_target_event.clear()

                last_switch_time = time.time()
                threading.Thread(
                    target=doubao_ai_interaction,
                    args=(CHARACTER_CONFIG[target], target),
                    daemon=True,
                ).start()
                talk_stable = 0
                talk_last_cls = None

            cv2.putText(
                frame,
                f"Talking: {now_target}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 255),
                2,
            )

        if HAS_DISPLAY:
            cv2.imshow("Figure", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    picam2.stop()
    if HAS_DISPLAY:
        cv2.destroyAllWindows()
    is_running = False


def signal_handler(sig, frame):
    global is_running
    print("\n🛑 退出中...")
    is_running = False
    program_exit_event.set()
    play_buffer.put("EOF")
    time.sleep(0.5)
    sys.exit(0)


def main():
    _require_env("DOUBAO_APP_ID", "DOUBAO_ACCESS_KEY", "DOUBAO_APP_KEY")

    print("🚀 启动系统...")
    pysignal.signal(pysignal.SIGINT, signal_handler)
    pysignal.signal(pysignal.SIGTERM, signal_handler)

    threading.Thread(target=audio_play_thread, daemon=True).start()
    threading.Thread(target=record_mic_to_queue, daemon=True).start()
    threading.Thread(target=yolo_detection_thread, daemon=True).start()

    print("✅ 启动完成！按 q 退出")
    while is_running:
        time.sleep(1)


if __name__ == "__main__":
    main()
