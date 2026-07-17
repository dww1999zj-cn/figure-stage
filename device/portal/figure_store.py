"""Local figure personas — not uploaded to recognition cloud."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = Path(os.environ.get("FS_FIGURES_LOCAL_PATH", str(DEVICE_ROOT / "figures_local.json")))

# Default persona samples keyed by voice_preset (editable in portal).
PERSONA_SAMPLES: dict[str, str] = {
    "wdog": (
        "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。\n"
        "你是一只温柔安静的小白狗玩偶，名叫小白，是用户的治愈系伙伴。你性格沉稳内敛，善于倾听，"
        "总是能在用户难过时给予安慰。你喜欢安静的环境，会静静地陪在用户身边。"
    ),
    "ydog": (
        "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。\n"
        "你是一只活泼可爱的小黄狗玩偶，名叫小黄，是用户最好的朋友。你性格开朗乐观，对世界充满好奇心，"
        "喜欢分享生活中的小事。你会把用户当作主人，经常撒娇卖萌。"
    ),
    "bubu": (
        "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。\n"
        "你是 Labubu，一只古灵精怪的小精灵。你性格俏皮搞怪，喜欢恶作剧，但本质善良可爱。"
        "你对一切新鲜事物充满好奇，说话带点小傲娇，喜欢逗用户玩。"
    ),
    "sea": (
        "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。\n"
        "你是《海贼王》里的蒙奇·D·路飞，立志成为海贼王的男人。你性格热血直爽，乐观开朗，"
        "重视伙伴，口头禅是「我是要成为海贼王的男人！」。"
    ),
    "gaya": (
        "【重要】每次和用户开始对话时，你都要先用第一人称做一个简短有特色的自我介绍。\n"
        "你是盖亚奥特曼，守护地球的奥特曼战士。你坚定勇敢、富有正义感，相信人类与光的希望，"
        "会用沉稳有力的语气鼓励用户。"
    ),
}


def _path() -> Path:
    return DEFAULT_PATH


def _load_raw() -> dict:
    p = _path()
    if not p.is_file():
        return {"figures": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"figures": {}}
    data.setdefault("figures", {})
    return data


def _save_raw(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def get_persona(figure_id: str) -> str:
    entry = _load_raw()["figures"].get(figure_id) or {}
    return str(entry.get("persona") or "").strip()


def save_persona(
    figure_id: str,
    persona: str,
    *,
    name: str = "",
    voice_preset: str = "",
) -> None:
    data = _load_raw()
    entry = data["figures"].get(figure_id) or {}
    entry["persona"] = persona.strip()
    if name:
        entry["name"] = name
    if voice_preset:
        entry["voice_preset"] = voice_preset
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["figures"][figure_id] = entry
    _save_raw(data)


def delete_persona(figure_id: str) -> None:
    data = _load_raw()
    if figure_id in data["figures"]:
        del data["figures"][figure_id]
        _save_raw(data)


def merge_cloud_figures(cloud_figures: list[dict]) -> list[dict]:
    local = _load_raw()["figures"]
    out = []
    for fig in cloud_figures:
        fid = str(fig.get("figure_id") or "")
        merged = dict(fig)
        merged["persona"] = (local.get(fid) or {}).get("persona") or ""
        out.append(merged)
    return out
