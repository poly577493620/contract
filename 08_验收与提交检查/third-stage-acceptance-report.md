# 课题四第三阶段验收报告

## 验收结论

第三阶段工程改造和限定门禁已完成。所有采购结果/合同模型输入已切换为“PDF 完整页面渲染 → 本地 OCR → 坐标阅读顺序恢复”，没有 PDF 文本层静默回退。005、006 的四份文档均通过 OCR 质量门禁；GLM 与 Kimi 通过两个样本的技术门禁，DeepSeek 在 006 因输出协议不完整未通过。已按要求停止，未运行全量评测。

## 已完成改动

1. 新增 PyMuPDF 300/360 DPI 页面渲染、RapidOCR/ONNX Runtime 本地识别、bbox/置信度/阅读顺序和证据映射。
2. 新增按 PDF、渲染器、DPI、OCR 引擎和阈值隔离的缓存；记录 PDF、页面图片和 OCR 文本 SHA-256。
3. 新增 OCR 质量门禁：300 DPI 失败时只重试一次 360 DPI；再次失败返回 `ocr_quality_failed`，不回退文本层。
4. 三模型共用预解析的同一份 OCR 文本；评测 manifest 记录相同输入哈希。
5. 模型参数白名单支持 thinking、reasoning effort、独立超时、输出预算和最多 2 次传输级重试。
6. 提示词升级为 `2026-08-28.v5`，同条款子要求合并，培训/应急/响应时限归入 `value_added_service`，证据逐字回查 OCR 原文。
7. Gold `verified-v1` 共 31 条全部通过门禁，包含用户确认的 005 `contract_term/missing`。

## OCR 实测与抽检

- 005 采购结果：1,150 字，中位置信度 0.99976，低置信比例 0%。
- 005 合同：2,931 字，中位置信度 0.99926，低置信比例 0.56%。
- 006 采购结果：1,145 字，中位置信度 0.99981，低置信比例 1.22%。
- 006 合同：2,823 字，中位置信度 0.99893，低置信比例 0%。
- 人工视觉抽检确认 005 的合同期限、15 日/30 日付款条件，以及 006 的培训、应急服务和响应时限。

抽检材料位于 `outputs/qa/ocr-v1-005-006/`，完整 OCR 缓存位于 `outputs/ocr-cache/`。

## 测试结果

- RapidOCR 官方环境检查：通过。
- Ruff format/check：通过。
- Mypy strict：通过。
- pytest：72 passed，2 skipped；跳过项为默认不运行的真实 OCR 集成测试。
- 显式 OCR 集成测试：2 passed；首次真实识别约 115.65 秒，缓存复跑 1.38 秒。
- Gold 校验：31/31 已核验，`ready_for_metrics=true`。

## 限定门禁结果

| 模型 | 成功运行 | 技术门禁 | Micro F1 | 主要差异 |
|---|---:|---|---:|---|
| `glm-5.3-flash` | 2/2 | 通过 | 0.857 | 006 多报 `acceptance/missing` |
| `deepseek-v4-flash` | 1/2 | 未通过 | 0.800 | 006 的问题编码未列入覆盖清单，整次运行失败 |
| `kimi-k3` | 2/2 | 通过 | 1.000 | 本轮与 3 条 Gold 问题一致 |

本表只覆盖 2 个样本、每组合 1 次，不是正式选型排名。完整结果位于 `outputs/evaluations/three-model-gate-ocr-v1-005-006/`。

## 兼容性调整

第三阶段提示词原建议关闭 GLM thinking；执行时官方文档已说明 GLM-5.3/GLM-5.3-Flash 不支持关闭，因此最小调整为 `thinking.enabled + reasoning_effort=low`。DeepSeek 仍关闭 thinking，Kimi 不发送该字段。该调整没有扩大授权范围。

## 下一步需用户决定

是否针对 DeepSeek 006 做一次新的、小范围修复轮次：强化 `items[].clause_code` 必须出现在 `covered_clause_codes` 的提示约束，然后仅复测 DeepSeek 006。未得到新的明确确认前，不修改模型名单、不补跑门禁、不启动 001–020 正式横评。

## 第四阶段追加记录：DeepSeek 006 单点复测

用户于 2026-08-30 明确确认第四阶段提示词后，项目将提示词升级为 `2026-08-30.v6`。修改仅增加通用覆盖清单集合不变量、输出前自检和非课题数据 few-shot；严格校验器和 Gold 未修改。

本次只新增 1 次 `deepseek-v4-flash × 006` 计分运行。复测首次请求成功、传输重试 0 次、耗时 58,979 ms；Schema、问题编码覆盖关系和 OCR 原文证据校验通过。预测 `value_added_service/missing` 与 006 Gold 一致，TP=1、FP=0、FN=0、F1=1.0。

仍保留“未覆盖 `acceptance`、`warranty_period`”警告，故该结果只证明原协议遗漏已修复，不代表整体条款覆盖或正式模型排名。006 继续使用第三阶段相同的页面 OCR 文本哈希。完整产物位于 `outputs/evaluations/deepseek-006-protocol-recheck-v1/`；复测后已停止，没有调用其他模型或运行全量评测。
