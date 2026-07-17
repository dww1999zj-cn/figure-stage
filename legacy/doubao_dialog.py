"""豆包 Realtime StartSession dialog 构建（stage_feature.py / test_doubao_voice.py 共用）。"""

from __future__ import annotations

import os
from typing import Any

from luckin_order import luckin_system_hint


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def build_dialog_extra(*, enable_music: bool = True) -> dict[str, Any]:
    """dialog.extra；.env 中 DOUBAO_ENABLE_WEBSEARCH=true 时开启联网。"""
    extra: dict[str, Any] = {
        "input_mod": os.environ.get("DOUBAO_INPUT_MOD", "keep_alive"),
        "model": os.environ.get("DOUBAO_MODEL", "1.2.1.1"),
        "enable_user_query_exit": True,
        "enable_music": enable_music,
    }
    if not _truthy("DOUBAO_ENABLE_WEBSEARCH"):
        return extra

    api_key = os.environ.get("DOUBAO_WEBSEARCH_API_KEY") or os.environ.get("VOLC_WEBSEARCH_API_KEY")
    if not api_key:
        raise ValueError("DOUBAO_ENABLE_WEBSEARCH=true 但未设置 DOUBAO_WEBSEARCH_API_KEY")

    search_type = os.environ.get("DOUBAO_WEBSEARCH_TYPE", "web_agent")
    extra["enable_volc_websearch"] = True
    extra["volc_websearch_type"] = search_type
    extra["volc_websearch_api_key"] = api_key

    if search_type == "web_agent":
        bot_id = os.environ.get("DOUBAO_WEBSEARCH_BOT_ID") or os.environ.get("VOLC_WEBSEARCH_BOT_ID")
        if not bot_id:
            raise ValueError("DOUBAO_WEBSEARCH_TYPE=web_agent 需要 DOUBAO_WEBSEARCH_BOT_ID")
        extra["volc_websearch_bot_id"] = bot_id

    count = os.environ.get("DOUBAO_WEBSEARCH_RESULT_COUNT")
    if count:
        extra["volc_websearch_result_count"] = int(count)

    no_result = os.environ.get("DOUBAO_WEBSEARCH_NO_RESULT_MESSAGE")
    if no_result:
        extra["volc_websearch_no_result_message"] = no_result

    return extra


def build_dialog(character: dict, *, enable_music: bool = True) -> dict[str, Any]:
    system_role = character["prompt"]
    hint = luckin_system_hint()
    if hint:
        system_role = f"{system_role}\n{hint}"

    dialog: dict[str, Any] = {
        "bot_name": character["name"],
        "system_role": system_role,
        "speaking_style": character["speaking_style"],
        "extra": build_dialog_extra(enable_music=enable_music),
    }
    city = os.environ.get("DOUBAO_LOCATION_CITY")
    if city:
        dialog["location"] = {
            "city": city,
            "country": os.environ.get("DOUBAO_LOCATION_COUNTRY", "中国"),
            "country_code": os.environ.get("DOUBAO_LOCATION_COUNTRY_CODE", "CN"),
        }
        province = os.environ.get("DOUBAO_LOCATION_PROVINCE")
        if province:
            dialog["location"]["province"] = province
    return dialog
