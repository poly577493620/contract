# DeepSeek 006 协议单点复测报告

## 结论

`deepseek-v4-flash` 使用提示词 `2026-08-30.v6` 完成唯一一次 006 复测，核心技术门禁通过。模型返回的 `value_added_service` 问题已同时列入 `covered_clause_codes`，Schema、重复编码和 OCR 原文证据校验均通过。

校验状态为“通过但有警告”：模型未在覆盖清单中声明 `acceptance`、`warranty_period`。这不影响本次问题项的结构和证据有效性，但说明条款覆盖完整性仍应人工关注。

## 输入一致性

- OCR 来源：`page_image`。
- 采购结果 OCR SHA-256：`7f3cc23fc4da85619edd4a90e2cd220205d4af9d302896172c53918c4200c374`。
- 合同 OCR SHA-256：`d15b5be1b0895ca393be60702be2b52aca965d383eec825bd87deba0cf4007c8`。
- 300 DPI，质量门禁通过，缓存键与第三阶段一致。
- 没有读取 PDF 文本层，也没有向模型发送页面图片。

## 运行记录

| 项目 | 结果 |
|---|---|
| 模型 | `deepseek-v4-flash` |
| 计分运行 | 1 |
| HTTP | 200 |
| 传输重试 | 0 |
| 耗时 | 58,979 ms |
| 输入 token | 3,919 |
| 输出 token | 9,097 |
| finish reason | `stop` |
| 估算成本 | 未配置，保持未知 |

兼容端点报告的输出 token 可能包含隐藏推理用量，因此高于 4,096 的可见输出配置值；报告只记录端点返回的 usage，不据此伪造成本。

## 与 Gold 对照

预测和 006 Gold 均为 `value_added_service/missing`：TP=1、FP=0、FN=0，Precision=Recall=F1=1.0。该结果只代表单样本、单次协议复测，不构成三模型总体排名或正式选型结论。

## 停止状态

复测完成后已停止。没有调用 GLM、Kimi、替代模型或其他样本，没有修改 Gold，也没有启动 001—020 全量评测。
