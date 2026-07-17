"""Short Doubao Realtime ASR burst for name wake (same credentials as dialog)."""

from __future__ import annotations

import json
import os
import time
import uuid

from websockets.sync.client import connect

DOUBAO_APP_ID = os.environ.get("DOUBAO_APP_ID", "")
DOUBAO_ACCESS_KEY = os.environ.get("DOUBAO_ACCESS_KEY", "")
DOUBAO_APP_KEY = os.environ.get("DOUBAO_APP_KEY", "")
DOUBAO_WS_URL = os.environ.get(
    "DOUBAO_WS_URL", "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
)
DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "1.2.1.1")

CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
MSG_WITH_EVENT = 0b0100
JSON = 0b0001
NO_COMPRESSION = 0b0000

EVENT_START_CONNECTION = 1
EVENT_START_SESSION = 100
EVENT_TASK_REQUEST = 200
EVENT_SESSION_STARTED = 150
EVENT_ASR_RESPONSE = 451
EVENT_ASR_ENDED = 459
EVENT_FINISH_SESSION = 102


def _header(message_type: int, *, is_json: bool = True) -> bytes:
    serial = JSON if is_json else NO_COMPRESSION
    return bytes(
        [
            (0b0001 << 4) | 0x01,
            (message_type << 4) | MSG_WITH_EVENT,
            (serial << 4) | NO_COMPRESSION,
            0x00,
        ]
    )


def _frame(message_type: int, event_id: int, session_id: str | None, payload: bytes, *, is_json: bool) -> bytes:
    body = bytearray()
    body.extend(event_id.to_bytes(4, "big"))
    if session_id:
        sid = session_id.encode("utf-8")
        body.extend(len(sid).to_bytes(4, "big"))
        body.extend(sid)
    body.extend(len(payload).to_bytes(4, "big"))
    body.extend(payload)
    return _header(message_type, is_json=is_json) + bytes(body)


def _parse(res: bytes | str) -> dict:
    if isinstance(res, str) or len(res) < 4:
        return {}
    message_type = res[1] >> 4
    flags = res[1] & 0x0F
    payload = res[4:]
    out: dict = {"message_type": ""}
    if message_type == 0b1001:
        out["message_type"] = "SERVER_FULL_RESPONSE"
    elif message_type == 0b1011:
        out["message_type"] = "SERVER_ACK"
    elif message_type == 0b1111:
        out["message_type"] = "SERVER_ERROR"
    if flags & MSG_WITH_EVENT and len(payload) >= 4:
        out["event"] = int.from_bytes(payload[:4], "big")
        payload = payload[4:]
    if len(payload) >= 4:
        sid_len = int.from_bytes(payload[:4], "big")
        if len(payload) >= 4 + sid_len:
            payload = payload[4 + sid_len :]
    if len(payload) >= 4:
        data_len = int.from_bytes(payload[:4], "big")
        if len(payload) >= 4 + data_len:
            out["payload_msg"] = payload[4 : 4 + data_len]
    return out


def transcribe_short_utterance(pcm_16k: bytes, timeout_sec: float = 8.0) -> str:
    """Send one short PCM clip (s16le mono 16kHz) to Doubao Realtime; return ASR text."""
    if not pcm_16k:
        return ""
    if not (DOUBAO_APP_ID and DOUBAO_ACCESS_KEY and DOUBAO_APP_KEY):
        print("[wake] 缺少豆包凭证，无法 ASR")
        return ""

    session_id = str(uuid.uuid4())
    ws = None
    last_text = ""
    try:
        ws = connect(
            DOUBAO_WS_URL,
            additional_headers={
                "X-Api-App-ID": DOUBAO_APP_ID,
                "X-Api-Access-Key": DOUBAO_ACCESS_KEY,
                "X-Api-Resource-Id": os.environ.get("DOUBAO_RESOURCE_ID", "volc.speech.dialog"),
                "X-Api-App-Key": DOUBAO_APP_KEY,
                "X-Api-Connect-Id": str(uuid.uuid4()),
            },
            close_timeout=1,
        )
        ws.send(_frame(CLIENT_FULL_REQUEST, EVENT_START_CONNECTION, None, b"{}", is_json=True))

        cfg = {
            "asr": {"extra": {"end_smooth_window_ms": 600, "enable_custom_vad": True}},
            "tts": {
                "speaker": "zh_female_xiaohe_jupiter_bigtts",
                "audio_config": {
                    "channel": 1,
                    "format": "pcm_s16le",
                    "sample_rate": 24000,
                },
            },
            "dialog": {
                "bot_name": "wake",
                "system_role": "你只负责听用户说话，不要说话，不要回复。",
                "speaking_style": "保持沉默。",
                "extra": {
                    "input_mod": "keep_alive",
                    "model": DOUBAO_MODEL,
                    "enable_user_query_exit": False,
                    "enable_music": False,
                },
            },
        }
        ws.send(
            _frame(
                CLIENT_FULL_REQUEST,
                EVENT_START_SESSION,
                session_id,
                json.dumps(cfg, ensure_ascii=False).encode("utf-8"),
                is_json=True,
            )
        )

        started = False
        deadline = time.time() + timeout_sec
        while time.time() < deadline and not started:
            resp = _parse(ws.recv(timeout=1))
            if resp.get("event") == EVENT_SESSION_STARTED:
                started = True
                break

        if not started:
            return ""

        chunk = 3200  # 100ms @ 16k
        for i in range(0, len(pcm_16k), chunk):
            ws.send(
                _frame(
                    CLIENT_AUDIO_ONLY_REQUEST,
                    EVENT_TASK_REQUEST,
                    session_id,
                    pcm_16k[i : i + chunk],
                    is_json=False,
                )
            )

        asr_deadline = time.time() + timeout_sec
        while time.time() < asr_deadline:
            try:
                resp = _parse(ws.recv(timeout=1))
            except Exception:
                continue
            evt = resp.get("event")
            pay = resp.get("payload_msg", b"")
            if evt == EVENT_ASR_RESPONSE and pay:
                try:
                    result = json.loads(pay)["results"][0]
                    text = str(result.get("text") or "").strip()
                    if text:
                        last_text = text
                except Exception:
                    pass
            elif evt == EVENT_ASR_ENDED:
                break

        try:
            ws.send(_frame(CLIENT_FULL_REQUEST, EVENT_FINISH_SESSION, session_id, b"{}", is_json=True))
        except Exception:
            pass
        return last_text.strip()
    except Exception as e:
        print(f"[wake] 豆包 ASR 失败: {e}")
        return ""
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
