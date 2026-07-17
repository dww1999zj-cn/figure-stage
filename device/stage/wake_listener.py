"""Name wake — local energy VAD + short Doubao ASR burst (light traffic).

Shares the stage mic queue (16 kHz PCM). Does not open a second InputStream
(USB audio devices are typically exclusive).
Only the registered figure currently identified on stage can be woken by name.
"""

from __future__ import annotations

import os
import queue
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from doubao_wake_asr import transcribe_short_utterance

WAKE_SAMPLE_RATE = 16000
WAKE_FIGURES_REFRESH_SEC = 30.0
WAKE_COOLDOWN_SEC = float(os.environ.get("WAKE_COOLDOWN_SEC", "3"))
WAKE_MAX_RECORD_SEC = float(os.environ.get("WAKE_MAX_RECORD_SEC", "3.5"))
WAKE_SPEECH_RMS = float(os.environ.get("WAKE_SPEECH_RMS", "450"))
WAKE_SILENCE_RMS = float(os.environ.get("WAKE_SILENCE_RMS", "280"))
WAKE_SILENCE_FRAMES = int(os.environ.get("WAKE_SILENCE_FRAMES", "8"))


def _normalize(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"):
            keep.append(ch)
    return "".join(keep).lower()


def _match_figure_name(text: str, names: list[str]) -> str | None:
    norm = _normalize(text)
    if not norm:
        return None
    for name in sorted(names, key=len, reverse=True):
        if _normalize(name) in norm:
            return name
    return None


def _rms(chunk: bytes) -> float:
    arr = np.frombuffer(chunk, dtype=np.int16)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))


def load_wake_figures() -> tuple[list[str], dict[str, dict[str, Any]]]:
    """All registered figures (name → meta). Wake still requires on-stage match."""
    try:
        from cloud_client import list_figures
        from figure_store import merge_cloud_figures

        figs = merge_cloud_figures(list_figures())
    except Exception as e:
        print(f"[wake] 无法加载手办列表: {e}")
        return [], {}

    names: list[str] = []
    mapping: dict[str, dict[str, Any]] = {}
    for fig in figs:
        name = str(fig.get("name") or "").strip()
        if not name or name in mapping:
            continue
        names.append(name)
        mapping[name] = fig
    return names, mapping


def _drain_queue(pcm_queue: queue.Queue) -> None:
    while True:
        try:
            pcm_queue.get_nowait()
        except queue.Empty:
            break


def _read_chunk(pcm_queue: queue.Queue, timeout: float = 0.2) -> bytes | None:
    try:
        return pcm_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def _record_utterance_from_queue(pcm_queue: queue.Queue) -> bytes | None:
    """Collect PCM from shared mic queue until silence after speech."""
    deadline = time.time() + WAKE_MAX_RECORD_SEC
    buf = bytearray()
    speech_seen = False
    silence_run = 0

    while time.time() < deadline:
        chunk = _read_chunk(pcm_queue, timeout=0.15)
        if chunk is None:
            if speech_seen:
                silence_run += 1
                if silence_run >= WAKE_SILENCE_FRAMES:
                    break
            continue

        level = _rms(chunk)
        if level >= WAKE_SPEECH_RMS:
            speech_seen = True
            silence_run = 0
            buf.extend(chunk)
        elif speech_seen:
            buf.extend(chunk)
            if level < WAKE_SILENCE_RMS:
                silence_run += 1
                if silence_run >= WAKE_SILENCE_FRAMES:
                    break
            else:
                silence_run = 0

    # ~80ms at 16k mono int16 minimum
    if not speech_seen or len(buf) < WAKE_SAMPLE_RATE * 2 // 12:
        return None
    return bytes(buf)


def run_name_wake_thread(
    *,
    is_running: Callable[[], bool],
    is_talking: Callable[[], bool],
    object_present: Callable[[], bool],
    allowed_names: Callable[[], list[str]],
    on_wake: Callable[[dict[str, Any]], None],
    pcm_queue: queue.Queue,
    drain_pcm: Callable[[], None] | None = None,
    audio_device_id: int = 0,  # unused; kept for call-site compat
) -> None:
    enabled = os.environ.get("FS_NAME_WAKE", "true").strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        print("[wake] 名字唤醒已关闭 (FS_NAME_WAKE=false)")
        return

    print(
        "[wake] 名字唤醒：共用麦克风队列 + 豆包短句 ASR；"
        "仅台上已识别的注册手办可用其名字唤起"
    )

    registered_names: list[str] = []
    name_to_figure: dict[str, dict[str, Any]] = {}
    last_refresh = 0.0
    last_wake_at = 0.0
    last_listen_log = ""

    def refresh_figures() -> bool:
        nonlocal registered_names, name_to_figure, last_refresh
        registered_names, name_to_figure = load_wake_figures()
        last_refresh = time.time()
        if registered_names:
            print(f"[wake] 已注册手办: {', '.join(registered_names)}")
        return bool(registered_names)

    def drain() -> None:
        if drain_pcm is not None:
            drain_pcm()
        else:
            _drain_queue(pcm_queue)

    refresh_figures()

    while is_running():
        if is_talking():
            drain()
            time.sleep(0.15)
            continue
        if not object_present():
            drain()
            time.sleep(0.25)
            continue
        if time.time() - last_refresh > WAKE_FIGURES_REFRESH_SEC:
            refresh_figures()
        if not registered_names:
            time.sleep(1.0)
            continue
        if time.time() - last_wake_at < WAKE_COOLDOWN_SEC:
            drain()
            time.sleep(0.2)
            continue

        listen_names = [n for n in allowed_names() if n in name_to_figure]
        if not listen_names:
            time.sleep(0.3)
            continue

        listen_key = ",".join(listen_names)
        if listen_key != last_listen_log:
            print(f"[wake] 当前可唤醒: {listen_key}")
            last_listen_log = listen_key
            drain()

        try:
            while is_running() and not is_talking() and object_present():
                if time.time() - last_refresh > WAKE_FIGURES_REFRESH_SEC:
                    refresh_figures()

                listen_names = [n for n in allowed_names() if n in name_to_figure]
                if not listen_names:
                    break

                chunk = _read_chunk(pcm_queue, timeout=0.2)
                if chunk is None:
                    continue
                if _rms(chunk) < WAKE_SPEECH_RMS:
                    continue

                # Speech started — keep this chunk and record the rest
                pcm_parts = [chunk]
                rest = _record_utterance_from_queue(pcm_queue)
                if rest:
                    pcm_parts.append(rest)
                pcm = b"".join(pcm_parts)
                if len(pcm) < WAKE_SAMPLE_RATE * 2 // 12:
                    continue

                print("[wake] 检测到说话，豆包 ASR 识别中…")
                text = transcribe_short_utterance(pcm)
                if not text:
                    continue
                print(f"[wake] ASR: {text!r}")

                listen_names = [n for n in allowed_names() if n in name_to_figure]
                hit = _match_figure_name(text, listen_names)
                if not hit:
                    print("[wake] 忽略：未喊到台上该手办的名字")
                    continue

                print(f"[wake] 听到台上手办名「{hit}」")
                last_wake_at = time.time()
                on_wake(name_to_figure[hit])
                drain()
                time.sleep(WAKE_COOLDOWN_SEC)
                break
        except Exception as e:
            print(f"[wake] 监听异常: {e}")
            time.sleep(2.0)
