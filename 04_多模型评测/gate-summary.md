# 第三阶段三模型技术门禁摘要

- 范围：005、006 × `glm-5.3-flash`、`deepseek-v4-flash`、`kimi-k3` × 1 次，共 6 个计分运行。
- 输入：完整 PDF 页面图片经本地 RapidOCR 识别；4 份 PDF 均在 300 DPI 通过质量门禁。
- 一致性：同一样本三个模型的 OCR 文本 SHA-256 一致，manifest 标记 `ocr_source=page_image`。
- 通过：GLM 005/006；Kimi 005/006；DeepSeek 005。
- 失败：DeepSeek 006 返回非空内容，但 `value_added_service` 未列入 `covered_clause_codes`，协议与证据校验失败。
- 指标：GLM Micro F1 0.857；DeepSeek 0.800、失败率 50%；Kimi 1.000。
- 仲裁：GLM 006 多报 `acceptance/missing`；DeepSeek 006 的 Gold 问题因整次运行失败按漏报计入。
- 结论：三模型整体技术门禁未全通过；已停止，未运行 001–020 全量评测，未自动替换任何模型。

完整数据见 `model_evaluation.json`、`runs.csv`、`run-manifest.json`、`human-arbitration.csv` 和 `raw/`。
