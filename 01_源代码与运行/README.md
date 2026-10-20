# 智审合规助手

面向采购结果与采购合同的双文档智能质检 MVP。项目可以离线完成 PDF 校验、逐页图片 OCR、条款候选提取、确定性三分类复核，以及 DOCX/PDF/JSON 报告导出；配置外部 OpenAI-compatible 端点后，可使用完全相同的 OCR 输入、提示词和评测口径运行至少三个模型。

> **教学模拟/测试数据。** 仅使用脱敏或经授权的文件；所有输出都是辅助复核意见，不构成法律、审计或生产审批结论。

## 已实现能力

- 分角色上传采购结果与采购合同 PDF，校验扩展名、签名、大小、页数和加密状态。
- 使用 PyMuPDF 将每页完整渲染为 PNG，再由本地 RapidOCR/ONNX Runtime 识别；模型输入不读取 PDF 文本层，也不静默回退。
- 按页保留 OCR 文本、bbox、置信度、阅读顺序、页面/PDF/OCR 文本哈希和稳定 chunk ID；缓存键包含渲染器、DPI、OCR 引擎和质量配置。
- 对金额、币种、数量、百分比、日期、工作日/自然日、否定和例外条件做确定性归一化；同类多商品、多服务和多时限不会被强制折叠成一个候选。
- 提供离线规则基线，以及异步、可重试、可计 token/成本的统一模型 provider；端点可分别配置 API 路径、温度、JSON 模式、最大输出 token、超时和重试。
- LLM 采用 v5 问题清单协议：`covered_clause_codes` 记录已检查条款，`items` 只返回 missing/different，最多 12 项；同一条款的多个子要求必须合并。provider、精确模型名、运行 ID、调用时间、提示词版本、耗时和 token 均由运行时代码写入；quote、页码和 chunk ID 会回查 OCR 原文。
- Streamlit 会区分离线/外部模型，外发前强制确认授权，并提供结论、风险、维度、置信度、人工复核筛选、解析/校验审计和受边界保护的当前会话清理。
- 固化 001–020 训练集、021–030 盲测集边界；未经人工核验的 gold 候选不能计算正式指标。

当前已完成 005/006 技术门禁与 DeepSeek 006 单点复测：005 的 Gold 包含付款账期差异和合同期限缺失；006 将免费培训、应急服务及响应时限合并为一个 `value_added_service/missing` 问题。四份输入 PDF 均通过 300 DPI 页面 OCR 质量门禁。第三阶段完成三模型 × 两样本 × 一次，共 6 个计分运行；第四阶段只追加一次 DeepSeek 006 复测，累计 7 个计分调用。未启动 001–020 正式横评。

## 安装

环境要求：Windows PowerShell、Python 3.12+、uv。

```powershell
Set-Location '<请替换为 submission\01_源代码与运行 的实际路径>'
uv sync --all-groups
uv run python -m smart_compliance --help
```

原始 ZIP 保留不变。如需从头重建数据目录：

```powershell
uv run python scripts/extract_dataset.py
```

## 运行

启动 Web 原型：

```powershell
uv run streamlit run src/smart_compliance/ui/app.py
```

离线解析单个 PDF：

```powershell
uv run python -m smart_compliance parse `
  --input 'data\raw\智审合规助手数据集\数据集_无水印\005\JZC-2026-005_采购结果.pdf' `
  --role procurement_result `
  --output 'outputs\parsed\005-result.json'
```

离线质检并导出三种格式：

```powershell
uv run python -m smart_compliance review `
  --result 'data\raw\智审合规助手数据集\数据集_无水印\005\JZC-2026-005_采购结果.pdf' `
  --contract 'data\raw\智审合规助手数据集\数据集_无水印\005\JZC-2026-005_采购合同.pdf' `
  --model rules `
  --output 'outputs\reviews\005'
```

## 外部模型配置与评测

复制 `.env.example` 为本地 `.env`，只在 `.env` 中设置 `SMART_COMPLIANCE_MODEL_ENDPOINTS`。值是 JSON 数组；除 `name`、`provider`、`model`、`base_url`、`api_key` 外，可显式设置 `api_path`、`temperature`、`response_format`、`max_output_tokens`、`timeout_seconds`、`max_retries`、`thinking` 和 `reasoning_effort`。参数经过白名单校验，不向不支持的模型透传。价格未知时不要填写价格字段。`.env` 已被 Git 忽略。

先从 001–020 参考答案生成候选：

```powershell
uv run python -m smart_compliance build-gold `
  --dataset-version 'data\raw\智审合规助手数据集\数据集_无水印' `
  --output 'data\gold\candidates.json'
```

候选默认 `human_verified=false`。当前经用户核验的版本化 Gold 为 `data/gold/verified-v1.json`，共 31 条，其中包含用户确认的 005 `contract_term/missing`。验证命令：

```powershell
uv run python -m smart_compliance validate-gold `
  --gold 'data\gold\verified-v1.json' `
  --manifest 'data\gold\verified-v1-manifest.json'
```

```powershell
uv run python -m smart_compliance evaluate `
  --dataset-version 'data\raw\智审合规助手数据集\数据集_无水印' `
  --samples '001-020' `
  --models 'model_a,model_b,model_c' `
  --gold 'data\gold\verified-v1.json' `
  --output 'outputs\evaluations\three-models'
```

第三阶段限定门禁使用提示词 `2026-08-28.v5` 和页面图片 OCR：`glm-5.3-flash`、`kimi-k3` 通过 005/006；`deepseek-v4-flash` 的 005 通过，006 因问题编码未同步列入覆盖清单而被严格判为失败。第四阶段使用通用集合不变量提示词 `2026-08-30.v6` 只复测 DeepSeek 006，Schema、证据与 Gold 对照通过，保留覆盖完整性警告。两样本单次门禁不能作为正式模型排名，详见 `docs/多模型评测报告.md`。

## 字段标注与盲测

生成字段级人工标注模板并查看当前状态：

```powershell
uv run python -m smart_compliance build-field-template `
  --dataset-version 'data\raw\智审合规助手数据集\数据集_无水印' `
  --samples '001-020' `
  --output 'data\annotations\field-annotations.csv'

uv run python -m smart_compliance evaluate-fields `
  --dataset-version 'data\raw\智审合规助手数据集\数据集_无水印' `
  --annotations 'data\annotations\field-annotations.csv' `
  --output 'outputs\qa\field-accuracy-status.json'
```

模板覆盖供应商、金额、数量、日期、期限、税率、否定词和服务范围。只有 `human_verified=true` 且填写期望值的行才进入准确率；当前 320 行均未标注，所以状态为“待标注”。`build-blind-manifest` 只为 021–030 记录输入哈希，标签和指标固定为空。

## 质量门禁

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src/
uv run pytest tests/ -v
```

带/无水印全量成对解析评估：

```powershell
uv run python scripts/evaluate_parsing.py `
  --dataset-root 'data\raw\智审合规助手数据集' `
  --output 'outputs\evaluations\parsing-quality.json'
```

## 关键目录

- `src/smart_compliance/`：解析、比对、模型、报告、评测和 UI 源码。
- `prompts/`：版本化系统提示词、用户模板与 few-shot。
- `config/`：条款别名、端点和价格配置示例。
- `outputs/reviews-phase2/005|006/`：第二阶段实际离线回归与审计报告。
- `outputs/qa/gold-v2/`：第二阶段保留的 30 条历史候选、审核表和覆盖清单；当前正式 Gold 以 `data/gold/verified-v1.json` 为准。
- `data/annotations/field-annotations.csv`：320 行待人工填写的字段标注模板。
- `outputs/blind/blind-manifest.json`：021–030 无标签输入哈希清单。
- `outputs/evaluations/parsing-quality.json`：120 份 PDF 的真实解析指标。
- `outputs/ocr-cache/`：按 PDF/引擎/渲染/配置哈希隔离的页面图片与 OCR 缓存。
- `outputs/qa/ocr-v1-005-006/`：005/006 的 OCR 抽检图片、文本与记录。
- `outputs/evaluations/three-model-gate-ocr-v1-005-006/`：第三阶段 6 次限定门禁的完整结果。
- `outputs/evaluations/deepseek-006-protocol-recheck-v1/`：第四阶段 DeepSeek 006 单点复测历史审计材料。
- `outputs/submission-samples/`：修复哈希显示和路径脱敏后重新生成的 005/006 样例报告。
- `docs/`：技术、合规、评测、录屏与个人心得填写材料。
- `evidence/acceptance-report.md`：第一阶段历史验收记录。
- `evidence/second-stage-acceptance-report.md`：第二阶段真实运行、失败和待办的验收记录。
- `evidence/third-stage-acceptance-report.md`：第三阶段 OCR、测试、门禁结论和下一步决策点。

## 已知限制

- 当前离线规则基线适合可确定的金额、日期、期限和缺失条款回归，复杂隐含语义仍应使用已授权模型并人工复核。
- OCR 仍可能在复杂表格、印章遮挡或低清扫描页上误识别；质量门禁失败时只以 360 DPI 重试一次，仍失败则终止该样本，不回退文本层。
- OCR 缓存按 PDF 内容哈希复用，不再因 Streamlit 将上传文件改名而重复渲染；页面 PNG 和缓存清单采用不覆盖式发布，避免 Windows 文件预览或并发解析导致 `cannot remove file ... Permission denied`。升级代码后如仍停留在旧错误页，按 `Ctrl+C` 停止旧 Streamlit 进程并重新启动，无需删除原始 PDF 或整批缓存。
- Web 原型使用会话目录保存上传与报告；演示后应只清理 `outputs/sessions/<明确会话 ID>`，不要对项目根目录执行递归删除。
- 当前只有 005/006 门禁和 DeepSeek 006 单点复测，不能据此形成正式排名。在用户确认 60 次单轮横评或 180 次三轮横评及费用上限前，不启动 001–020 正式评测。
