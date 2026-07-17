"""HTTP client for Figure Stage cloud recognition API."""

from __future__ import annotations

import os
from typing import Any

import httpx


class CloudError(RuntimeError):
    pass


def _base() -> str:
    return os.environ.get("CLOUD_BASE_URL", "").rstrip("/")


def _token() -> str:
    return os.environ.get("DEVICE_CLOUD_TOKEN", "").strip()


def _device_id() -> str:
    return os.environ.get("DEVICE_ID", "").strip()


def _headers() -> dict[str, str]:
    tok = _token()
    if not tok:
        raise CloudError("未配置 DEVICE_CLOUD_TOKEN（请在门户「凭证」页保存）")
    return {"Authorization": f"Bearer {tok}"}


def require_cloud_config() -> tuple[str, str]:
    base = _base()
    did = _device_id()
    if not base:
        raise CloudError("未配置 CLOUD_BASE_URL")
    if not did:
        raise CloudError("未配置 DEVICE_ID")
    _headers()
    return base, did


def list_figures() -> list[dict[str, Any]]:
    base, did = require_cloud_config()
    r = httpx.get(f"{base}/v1/devices/{did}/figures", headers=_headers(), timeout=30.0)
    if r.status_code >= 400:
        raise CloudError(f"list figures HTTP {r.status_code}: {r.text[:200]}")
    return r.json().get("figures") or []


def create_figure(
    name: str,
    voice_preset: str,
    jpeg_list: list[bytes],
) -> dict[str, Any]:
    base, did = require_cloud_config()
    files = [("images", (f"frame{i}.jpg", data, "image/jpeg")) for i, data in enumerate(jpeg_list)]
    data = {"name": name, "voice_preset": voice_preset}
    r = httpx.post(
        f"{base}/v1/devices/{did}/figures",
        headers=_headers(),
        data=data,
        files=files,
        timeout=120.0,
    )
    if r.status_code >= 400:
        raise CloudError(f"create figure HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def delete_figure(figure_id: str) -> None:
    base, did = require_cloud_config()
    r = httpx.delete(
        f"{base}/v1/devices/{did}/figures/{figure_id}",
        headers=_headers(),
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise CloudError(f"delete HTTP {r.status_code}: {r.text[:200]}")


def recognize_jpeg(jpeg: bytes) -> dict[str, Any]:
    base, did = require_cloud_config()
    r = httpx.post(
        f"{base}/v1/devices/{did}/recognize",
        headers=_headers(),
        files={"image": ("frame.jpg", jpeg, "image/jpeg")},
        timeout=60.0,
    )
    if r.status_code >= 400:
        raise CloudError(f"recognize HTTP {r.status_code}: {r.text[:300]}")
    return r.json()
