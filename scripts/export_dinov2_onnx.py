#!/usr/bin/env python3
"""Export DINOv2 ViT-S/14 to ONNX (run on a dev machine with PyTorch installed)."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Export dinov2_vits14 to ONNX for figure-stage")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.expanduser("~"), "Desktop", "dinov2_vits14.onnx"),
        help="Output ONNX path (default: ~/Desktop/dinov2_vits14.onnx)",
    )
    parser.add_argument("--opset", type=int, default=17, help="ONNX opset version")
    args = parser.parse_args()

    try:
        import torch
    except ImportError:
        print("需要 PyTorch: pip install torch", file=sys.stderr)
        sys.exit(1)

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    print("从 torch.hub 加载 dinov2_vits14（首次会下载权重）...")
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
    model.eval()

    class EmbedWrapper(torch.nn.Module):
        def __init__(self, backbone):
            super().__init__()
            self.backbone = backbone

        def forward(self, x):
            return self.backbone(x)

    wrapper = EmbedWrapper(model)
    dummy = torch.randn(1, 3, 224, 224, dtype=torch.float32)

    with torch.no_grad():
        sample = wrapper(dummy)
    print(f"输出 shape: {tuple(sample.shape)}（应为 384 维 embedding）")

    print(f"导出 ONNX -> {args.output}")
    export_kwargs = dict(
        input_names=["input"],
        output_names=["embedding"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=args.opset,
    )
    # PyTorch 2.x 默认 dynamo 导出在 GBK 终端可能因 emoji 日志崩溃，legacy 更稳
    try:
        torch.onnx.export(wrapper, dummy, args.output, dynamo=False, **export_kwargs)
    except TypeError:
        torch.onnx.export(wrapper, dummy, args.output, **export_kwargs)

    size_mb = os.path.getsize(args.output) / (1024 * 1024)
    print(f"完成: {args.output} ({size_mb:.1f} MB)")
    print("树莓派: 拷贝到 /home/pi/Desktop/dinov2_vits14.onnx（与 toy.pt 同目录）")
    print("或在 .env 设置 FEATURE_MODEL_PATH 指向实际路径")


if __name__ == "__main__":
    main()
