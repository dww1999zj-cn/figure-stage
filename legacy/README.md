# Legacy：本地 Pi + ONNX 原型（归档）

本目录冻结 **云改版之前** 的方案（分支 `archive/local-pi-v1`）。

**新装 / 复刻请只用仓库 [`device/`](../device/)**，并阅读 [`device/README.md`](../device/README.md)。

## 旧方案特点（仅对照）

- 树莓派本机跑 DINOv2 ONNX（`feature_embed.py`）
- 入口：`python stage_feature.py`
- 配网：`wifi_setup/`（已被 `device/portal` + `device/supervisor` 取代）

```bash
cd legacy
pip install -r requirements.txt
cp env.example .env
python stage_feature.py
```

主线说明见根目录 [README.md](../README.md)。
