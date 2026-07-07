"""DINOv2 embedding (ONNX) and registry matching for figure-stage."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
INPUT_SIZE = 224
VALID_TARGET_KEYS = frozenset({"bubu", "gaya", "wdog"})
MANIFEST_VERSION = 1


def _default_model_path() -> str:
    return "/home/pi/Desktop/dinov2_vits14.onnx"


def _default_registry_dir() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "registry")


def normalize_vector(vec: np.ndarray) -> np.ndarray:
    vec = vec.astype(np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    if norm < 1e-8:
        return vec
    return vec / norm


def centroid_from_embeddings(embeddings: np.ndarray) -> np.ndarray:
    return normalize_vector(np.mean(embeddings, axis=0))


class DINOv2Embedder:
    """Run DINOv2 ViT-S/14 ONNX and return L2-normalized embeddings."""

    def __init__(self, model_path: str | None = None):
        import onnxruntime as ort

        path = model_path or os.environ.get("FEATURE_MODEL_PATH") or _default_model_path()
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"未找到 ONNX 模型: {path}\n"
                "开发机导出: python scripts/export_dinov2_onnx.py --output ~/Desktop/dinov2_vits14.onnx\n"
                "树莓派默认路径与 toy.pt 相同: /home/pi/Desktop/dinov2_vits14.onnx"
            )

        providers = ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.model_path = path

    def preprocess(self, frame_rgb: np.ndarray, roi_fraction: float = 0.6) -> np.ndarray:
        h, w = frame_rgb.shape[:2]
        ch = max(1, int(h * roi_fraction))
        cw = max(1, int(w * roi_fraction))
        y0 = (h - ch) // 2
        x0 = (w - cw) // 2
        crop = frame_rgb[y0 : y0 + ch, x0 : x0 + cw]
        resized = cv2.resize(crop, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
        x = resized.astype(np.float32) / 255.0
        x = (x - IMAGENET_MEAN) / IMAGENET_STD
        x = np.transpose(x, (2, 0, 1))
        return np.expand_dims(x, axis=0).astype(np.float32)

    def embed(self, frame_rgb: np.ndarray, roi_fraction: float = 0.6) -> np.ndarray:
        batch = self.preprocess(frame_rgb, roi_fraction=roi_fraction)
        out = self.session.run(None, {self.input_name: batch})[0]
        return normalize_vector(out.reshape(-1))

    def embed_many(self, frames: list[np.ndarray], roi_fraction: float = 0.6) -> np.ndarray:
        if not frames:
            return np.zeros((0, 384), dtype=np.float32)
        rows = [self.embed(frame, roi_fraction=roi_fraction) for frame in frames]
        return np.stack(rows, axis=0)


def manifest_path(registry_dir: str) -> str:
    return os.path.join(registry_dir, "manifest.json")


def load_manifest(registry_dir: str) -> dict[str, Any]:
    path = manifest_path(registry_dir)
    if not os.path.isfile(path):
        return {"version": MANIFEST_VERSION, "model": "dinov2_vits14", "entries": {}}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("entries", {})
    return data


def save_manifest(registry_dir: str, manifest: dict[str, Any]) -> None:
    os.makedirs(registry_dir, exist_ok=True)
    with open(manifest_path(registry_dir), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def load_registry_entry(registry_dir: str, key: str) -> tuple[np.ndarray, np.ndarray]:
    npz_path = os.path.join(registry_dir, f"{key}.npz")
    if not os.path.isfile(npz_path):
        raise FileNotFoundError(f"未找到注册文件: {npz_path}")
    data = np.load(npz_path)
    embeddings = data["embeddings"].astype(np.float32)
    centroid = data["centroid"].astype(np.float32)
    return embeddings, normalize_vector(centroid)


def save_registry_entry(
    registry_dir: str,
    key: str,
    display_name: str,
    embeddings: np.ndarray,
    *,
    model_path: str,
) -> None:
    if key not in VALID_TARGET_KEYS:
        raise ValueError(f"无效 key: {key}，允许: {sorted(VALID_TARGET_KEYS)}")

    os.makedirs(registry_dir, exist_ok=True)
    centroid = centroid_from_embeddings(embeddings)
    npz_path = os.path.join(registry_dir, f"{key}.npz")
    np.savez_compressed(npz_path, embeddings=embeddings.astype(np.float32), centroid=centroid)

    manifest = load_manifest(registry_dir)
    manifest["version"] = MANIFEST_VERSION
    manifest["model"] = "dinov2_vits14"
    manifest["entries"][key] = {
        "name": display_name,
        "frames": int(len(embeddings)),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "model_path": os.path.basename(model_path),
    }
    save_manifest(registry_dir, manifest)


def delete_registry_entry(registry_dir: str, key: str) -> bool:
    npz_path = os.path.join(registry_dir, f"{key}.npz")
    removed = False
    if os.path.isfile(npz_path):
        os.remove(npz_path)
        removed = True

    manifest = load_manifest(registry_dir)
    if key in manifest.get("entries", {}):
        del manifest["entries"][key]
        removed = True
        save_manifest(registry_dir, manifest)
    return removed


def load_all_centroids(registry_dir: str) -> dict[str, np.ndarray]:
    manifest = load_manifest(registry_dir)
    centroids: dict[str, np.ndarray] = {}
    for key in manifest.get("entries", {}):
        try:
            _, centroid = load_registry_entry(registry_dir, key)
            centroids[key] = centroid
        except FileNotFoundError:
            continue
    return centroids


def match_embedding(
    query: np.ndarray,
    centroids: dict[str, np.ndarray],
    *,
    min_score: float,
    min_margin: float,
) -> tuple[str | None, float, float, list[tuple[str, float]]]:
    """Return (best_key, best_score, margin, ranked_scores)."""
    query = normalize_vector(query)
    if not centroids:
        return None, 0.0, 0.0, []

    ranked = sorted(
        ((key, float(np.dot(query, centroid))) for key, centroid in centroids.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    best_key, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - second_score

    if best_score < min_score or margin < min_margin:
        return None, best_score, margin, ranked
    return best_key, best_score, margin, ranked
