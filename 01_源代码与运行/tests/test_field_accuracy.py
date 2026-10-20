"""字段人工标注与盲测边界。"""

import csv
import json
from pathlib import Path

from smart_compliance.evaluation.field_accuracy import (
    FieldEvaluationRequest,
    build_blind_manifest,
    evaluate_field_annotations,
)


def test_unverified_annotations_report_pending(tmp_path: Path) -> None:
    """没有人工标注时只能显示待标注，不得伪造准确率。"""
    annotations = tmp_path / "annotations.csv"
    with annotations.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sample_id", "document_role", "field_type", "expected_value", "human_verified"])
        writer.writerow(["001", "procurement_result", "supplier", "", "false"])
    output = tmp_path / "field-accuracy.json"
    report = evaluate_field_annotations(
        FieldEvaluationRequest(
            dataset_version=tmp_path / "dataset",
            annotations_path=annotations,
            output_path=output,
            max_upload_mb=1,
            max_pdf_pages=1,
        )
    )
    assert report.status == "待标注"
    assert report.accuracy is None
    assert output.is_file()


def test_blind_manifest_contains_hashes_but_no_labels(tmp_path: Path) -> None:
    """021-030 清单只记录输入哈希，标签和指标必须为空。"""
    dataset = tmp_path / "dataset"
    for sample in range(21, 31):
        sample_dir = dataset / f"{sample:03d}"
        sample_dir.mkdir(parents=True)
        (sample_dir / f"FSC-{sample:03d}_采购结果.pdf").write_bytes(b"result")
        (sample_dir / f"FSC-{sample:03d}_采购合同.pdf").write_bytes(b"contract")
    output = build_blind_manifest(dataset, tmp_path / "blind-manifest.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["labeled"] is False
    assert len(payload["samples"]) == 10
    assert payload["samples"][0]["labels"] is None
    assert payload["samples"][0]["metrics"] is None
    assert len(payload["samples"][0]["inputs"]["contract"]["sha256"]) == 64
