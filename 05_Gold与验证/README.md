# Gold 数据说明

`candidates.json` 是第一阶段自动抽取的历史候选，不用于正式计分。用户已逐条审核参考答案，并明确要求把 005 的 `contract_term/missing` 计入 Gold。

当前正式版本为：

- `verified-v1.json`：001–020 共 31 条问题，全部 `human_verified=true`。
- `verified-v1-review.csv`：逐条审核记录。
- `verified-v1-manifest.json`：版本、审核依据、各样本数量与零问题样本清单。
- 零问题样本：011、014。
- 021–030：固定为盲测集，不写入 Gold，也不计算教师标签指标。

验证命令：

```powershell
uv run python -m smart_compliance validate-gold `
  --gold 'data\gold\verified-v1.json' `
  --manifest 'data\gold\verified-v1-manifest.json'
```

只有上述版本化文件可作为当前评测标签。任何后续更改都应新建版本、记录复核人和日期，不应覆盖已使用的 Gold。
