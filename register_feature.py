#!/usr/bin/env python3
"""
手办特征注册 — DINOv2 embedding → registry/

用法:
    # 开发机先导出 ONNX（仅需一次）
    pip install torch
    python scripts/export_dinov2_onnx.py

    # 树莓派 / 开发机注册
    python register_feature.py register --key ydog --name 小黄
    python register_feature.py list
    python register_feature.py delete --key ydog
    python register_feature.py verify --key ydog
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import cv2
import numpy as np

from feature_embed import (
    VALID_TARGET_KEYS,
    DINOv2Embedder,
    delete_registry_entry,
    load_all_centroids,
    load_manifest,
    load_registry_entry,
    match_embedding,
    save_registry_entry,
)

HAS_DISPLAY = os.environ.get("DISPLAY") is not None


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


_load_env_file()

DEFAULT_REGISTRY_DIR = os.environ.get("FEATURE_REGISTRY_DIR", "registry")
DEFAULT_FRAMES = int(os.environ.get("FEATURE_REGISTER_FRAMES", "24"))
DEFAULT_ROI = float(os.environ.get("FEATURE_ROI_FRACTION", "0.6"))
DEFAULT_MIN_SCORE = float(os.environ.get("FEATURE_MIN_SCORE", "0.60"))
DEFAULT_MIN_MARGIN = float(os.environ.get("FEATURE_MIN_MARGIN", "0.10"))
COUNTDOWN_SEC = int(os.environ.get("FEATURE_REGISTER_COUNTDOWN", "3"))


class CameraSource:
    def read_rgb(self) -> np.ndarray | None:
        raise NotImplementedError

    def release(self) -> None:
        pass


class Picamera2Source(CameraSource):
    def __init__(self):
        from picamera2 import Picamera2

        self.picam2 = Picamera2()
        cfg = self.picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 30},
        )
        self.picam2.configure(cfg)
        self.picam2.start()
        time.sleep(0.5)

    def read_rgb(self) -> np.ndarray | None:
        frame = self.picam2.capture_array()
        frame = cv2.flip(frame, 1)
        if frame.shape[2] == 4:
            frame = frame[..., :3]
        return frame

    def release(self) -> None:
        self.picam2.stop()


class OpenCVSource(CameraSource):
    def __init__(self, device: int):
        self.cap = cv2.VideoCapture(device)
        if not self.cap.isOpened():
            raise RuntimeError(f"无法打开摄像头 device={device}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        time.sleep(0.5)

    def read_rgb(self) -> np.ndarray | None:
        ok, frame = self.cap.read()
        if not ok:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def release(self) -> None:
        self.cap.release()


def open_camera(prefer: str, device: int) -> CameraSource:
    if prefer == "picamera2":
        return Picamera2Source()
    if prefer == "opencv":
        return OpenCVSource(device)
    try:
        return Picamera2Source()
    except Exception:
        return OpenCVSource(device)


def load_images_from_dir(image_dir: str) -> list[np.ndarray]:
    paths: list[str] = []
    for name in sorted(os.listdir(image_dir)):
        lower = name.lower()
        if lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
            paths.append(os.path.join(image_dir, name))
    if not paths:
        raise FileNotFoundError(f"目录内无图片: {image_dir}")

    frames: list[np.ndarray] = []
    for path in paths:
        bgr = cv2.imread(path)
        if bgr is None:
            print(f"[WARN] 跳过无法读取: {path}")
            continue
        frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if not frames:
        raise RuntimeError(f"无法从目录加载有效图片: {image_dir}")
    return frames


def capture_frames_live(
    source: CameraSource,
    frame_count: int,
    *,
    preview: bool,
) -> list[np.ndarray]:
    print(f"请将手办放在展示台中央，{COUNTDOWN_SEC} 秒后开始采集 {frame_count} 帧...")
    for sec in range(COUNTDOWN_SEC, 0, -1):
        print(f"  {sec}...")
        time.sleep(1)

    frames: list[np.ndarray] = []
    interval = 0.12
    for i in range(frame_count):
        frame = source.read_rgb()
        if frame is None:
            print("[WARN] 读取帧失败，跳过")
            time.sleep(interval)
            continue
        frames.append(frame.copy())
        print(f"  已采集 {len(frames)}/{frame_count}", end="\r")
        if preview and HAS_DISPLAY:
            show = frame.copy()
            cv2.putText(
                show,
                f"register {len(frames)}/{frame_count}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )
            cv2.imshow("Register-Figure", show)
            cv2.waitKey(1)
        time.sleep(interval)

    print()
    if preview and HAS_DISPLAY:
        cv2.destroyAllWindows()
    if len(frames) < max(4, frame_count // 3):
        raise RuntimeError(f"有效帧过少 ({len(frames)}/{frame_count})，请检查摄像头或手办位置")
    return frames


def cmd_list(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.registry_dir)
    entries = manifest.get("entries", {})
    if not entries:
        print("registry 为空，请先 register")
        return 0
    print(f"registry: {os.path.abspath(args.registry_dir)}")
    for key, meta in sorted(entries.items()):
        print(
            f"  {key:6s}  name={meta.get('name', '?')}  "
            f"frames={meta.get('frames', '?')}  at={meta.get('registered_at', '?')}"
        )
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    if delete_registry_entry(args.registry_dir, args.key):
        print(f"已删除: {args.key}")
        return 0
    print(f"未找到: {args.key}")
    return 1


def cmd_register(args: argparse.Namespace) -> int:
    if args.key not in VALID_TARGET_KEYS:
        print(f"无效 key: {args.key}，允许: {sorted(VALID_TARGET_KEYS)}", file=sys.stderr)
        return 1

    embedder = DINOv2Embedder(args.model_path)
    source = None

    try:
        if args.image_dir:
            print(f"从目录加载图片: {args.image_dir}")
            frames = load_images_from_dir(args.image_dir)
            if len(frames) > args.frames:
                step = len(frames) / args.frames
                frames = [frames[int(i * step)] for i in range(args.frames)]
        else:
            source = open_camera(args.camera, args.device)
            frames = capture_frames_live(source, args.frames, preview=not args.no_preview)

        print("提取 DINOv2 特征...")
        embeddings = embedder.embed_many(frames, roi_fraction=args.roi)
        save_registry_entry(
            args.registry_dir,
            args.key,
            args.name,
            embeddings,
            model_path=embedder.model_path,
        )
        print(
            f"注册完成: key={args.key} name={args.name} "
            f"frames={len(embeddings)} -> {args.registry_dir}/{args.key}.npz"
        )
        return 0
    finally:
        if source:
            source.release()


def cmd_verify(args: argparse.Namespace) -> int:
    embedder = DINOv2Embedder(args.model_path)
    centroids = load_all_centroids(args.registry_dir)
    if args.key not in centroids:
        print(f"registry 中无 key: {args.key}", file=sys.stderr)
        return 1

    try:
        _, expected_centroid = load_registry_entry(args.registry_dir, args.key)
    except FileNotFoundError:
        print(f"缺少 npz: {args.key}", file=sys.stderr)
        return 1

    source = None
    if args.image:
        bgr = cv2.imread(args.image)
        if bgr is None:
            print(f"无法读取图片: {args.image}", file=sys.stderr)
            return 1
        frames = [cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)]
    else:
        source = open_camera(args.camera, args.device)
        print(f"将 {args.key} 放在台上，按 Enter 开始单次验证...")
        input()
        frame = source.read_rgb()
        if frame is None:
            print("读取帧失败", file=sys.stderr)
            return 1
        frames = [frame]

    try:
        query = embedder.embed(frames[0], roi_fraction=args.roi)
        self_score = float(np.dot(query, expected_centroid))
        matched_key, best_score, margin, ranked = match_embedding(
            query,
            centroids,
            min_score=args.min_score,
            min_margin=args.min_margin,
        )

        print(f"与 {args.key} 自身相似度: {self_score:.3f}")
        print("与 registry 全部类别:")
        for key, score in ranked:
            mark = " <--" if key == matched_key else ""
            print(f"  {key}: {score:.3f}{mark}")
        print(f"margin(top1-top2): {margin:.3f}  阈值 score>={args.min_score} margin>={args.min_margin}")

        if matched_key == args.key:
            print(f"[OK] 识别为 {matched_key} (score={best_score:.3f})")
            return 0
        if matched_key:
            print(f"[MISMATCH] 识别为 {matched_key}，期望 {args.key}")
        else:
            print(f"[LOW CONF] 未达到阈值，最高 {ranked[0][0]}={ranked[0][1]:.3f}")
        return 1
    finally:
        if source:
            source.release()


def build_parser() -> argparse.ArgumentParser:
    default_model = os.environ.get("FEATURE_MODEL_PATH", "/home/pi/Desktop/dinov2_vits14.onnx")

    parser = argparse.ArgumentParser(description="手办 DINOv2 特征注册工具")
    parser.add_argument("--registry-dir", default=DEFAULT_REGISTRY_DIR, help="registry 目录")
    parser.add_argument("--model-path", default=default_model, help="DINOv2 ONNX 路径")
    parser.add_argument("--roi", type=float, default=DEFAULT_ROI, help="中心 ROI 占比 (0~1)")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-margin", type=float, default=DEFAULT_MIN_MARGIN)

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出已注册手办")
    p_list.set_defaults(func=cmd_list)

    p_del = sub.add_parser("delete", help="删除某类注册")
    p_del.add_argument("--key", required=True, choices=sorted(VALID_TARGET_KEYS))
    p_del.set_defaults(func=cmd_delete)

    p_reg = sub.add_parser("register", help="注册新手办特征")
    p_reg.add_argument("--key", required=True, choices=sorted(VALID_TARGET_KEYS))
    p_reg.add_argument("--name", required=True, help="显示名称，如 小黄")
    p_reg.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="采集帧数")
    p_reg.add_argument("--image-dir", help="从目录读图注册（开发机无摄像头时用）")
    p_reg.add_argument("--camera", choices=("auto", "picamera2", "opencv"), default="auto")
    p_reg.add_argument("--device", type=int, default=0, help="OpenCV 摄像头编号")
    p_reg.add_argument("--no-preview", action="store_true", help="不显示预览窗口")
    p_reg.set_defaults(func=cmd_register)

    p_ver = sub.add_parser("verify", help="验证某类是否匹配")
    p_ver.add_argument("--key", required=True, choices=sorted(VALID_TARGET_KEYS))
    p_ver.add_argument("--image", help="单张测试图（不指定则用摄像头）")
    p_ver.add_argument("--camera", choices=("auto", "picamera2", "opencv"), default="auto")
    p_ver.add_argument("--device", type=int, default=0)
    p_ver.set_defaults(func=cmd_verify)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
