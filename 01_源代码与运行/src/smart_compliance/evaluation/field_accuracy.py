"""字段级人工标注模板、解析准确率和无标签盲测清单。"""

import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from smart_compliance.domain.models import DocumentRole, ParsedDocument
from smart_compliance.evaluation.data_split import validate_gold_sample
from smart_compliance.evaluation.models import FieldAccuracyReport, FieldTypeAccuracy
from smart_compliance.parsing.pdf_parser import PdfParser

FIELD_TYPES = (
    "supplier",
    "amount",
    "quantity",
    "date",
    "duration",
    "tax_rate",
    "negation",
    "service_scope",
)
ROLE_FILE_PATTERNS = {
    DocumentRole.PROCUREMENT_RESULT: "*采购结果.pdf",
    DocumentRole.CONTRACT: "*采购合同.pdf",
}


@dataclass(frozen=True, slots=True)
class FieldEvaluationRequest:
    """字段准确率评测输入。"""

    dataset_version: Path
    annotations_path: Path
    output_path: Path
    max_upload_mb: int
    max_pdf_pages: int


def build_annotation_template(dataset_version: Path, sample_ids: list[str], output_path: Path) -> Path:
    """生成默认未核验、不可直接计算准确率的 CSV 模板。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "sample_id",
                "document_role",
                "source_file",
                "field_type",
                "expected_value",
                "expected_page",
                "human_verified",
                "reviewer",
                "notes",
            ]
        )
        for sample_id in sample_ids:
            validate_gold_sample(sample_id)
            for role, path in _role_files(dataset_version / sample_id).items():
                for field_type in FIELD_TYPES:
                    writer.writerow([sample_id, role.value, path.name, field_type, "", "", "false", "", ""])
    return output_path


def evaluate_field_annotations(request: FieldEvaluationRequest) -> FieldAccuracyReport:
    """用人工确认的期望值检查解析文本；无标注时明确返回“待标注”。"""
    rows = _read_annotations(request.annotations_path)
    verified = [row for row in rows if _is_true(row.get("human_verified", "")) and row.get("expected_value", "")]
    if not verified:
        report = FieldAccuracyReport(
            status="待标注",
            total_rows=len(rows),
            verified_rows=0,
            correct_rows=0,
            accuracy=None,
            by_field={},
            warnings=["没有 human_verified=true 且填写 expected_value 的人工标注，未计算解析准确率。"],
        )
        _save_report(report, request.output_path)
        return report
    parser = PdfParser(request.max_upload_mb, request.max_pdf_pages)
    cache: dict[tuple[str, DocumentRole], ParsedDocument] = {}
    results: dict[str, list[bool]] = defaultdict(list)
    for row in verified:
        sample_id = row["sample_id"]
        validate_gold_sample(sample_id)
        role = DocumentRole(row["document_role"])
        key = (sample_id, role)
        if key not in cache:
            source = _role_files(request.dataset_version / sample_id)[role]
            cache[key] = parser.parse(source, role)
        expected = _normalize(row["expected_value"])
        actual = _normalize(cache[key].cleaned_text)
        results[row["field_type"]].append(expected in actual)
    correct = sum(sum(matches) for matches in results.values())
    by_field = {
        field_type: FieldTypeAccuracy(
            verified=len(matches),
            correct=sum(matches),
            accuracy=sum(matches) / len(matches),
        )
        for field_type, matches in sorted(results.items())
    }
    report = FieldAccuracyReport(
        status="已评测",
        total_rows=len(rows),
        verified_rows=len(verified),
        correct_rows=correct,
        accuracy=correct / len(verified),
        by_field=by_field,
        warnings=["该指标仅衡量人工标注值能否在清洗文本中回查，不等同于端到端业务正确率。"],
    )
    _save_report(report, request.output_path)
    return report


def build_blind_manifest(dataset_version: Path, output_path: Path) -> Path:
    """为 021-030 生成无标签输入哈希清单，绝不读取参考答案。"""
    samples: list[dict[str, object]] = []
    for sample in range(21, 31):
        sample_id = f"{sample:03d}"
        files = _role_files(dataset_version / sample_id)
        samples.append(
            {
                "sample_id": sample_id,
                "inputs": {
                    role.value: {"source_file": path.name, "sha256": _sha256(path)} for role, path in files.items()
                },
                "labels": None,
                "metrics": None,
            }
        )
    payload = {
        "version": "blind-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_range": "021-030",
        "labeled": False,
        "caveat": "无标签盲测清单；不得计算准确率、写入 Gold 或据此反向调参。",
        "samples": samples,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _read_annotations(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {"sample_id", "document_role", "field_type", "expected_value", "human_verified"}
    if not rows:
        return []
    missing = required - set(rows[0])
    if missing:
        raise ValueError("字段标注文件缺少列: " + ", ".join(sorted(missing)))
    unknown = sorted({row["field_type"] for row in rows if row["field_type"] not in FIELD_TYPES})
    if unknown:
        raise ValueError("字段标注文件包含未知 field_type: " + ", ".join(unknown))
    return rows


def _role_files(sample_dir: Path) -> dict[DocumentRole, Path]:
    result: dict[DocumentRole, Path] = {}
    for role, pattern in ROLE_FILE_PATTERNS.items():
        matches = list(sample_dir.glob(pattern))
        if len(matches) != 1:
            raise ValueError(f"样本目录中 {pattern} 数量不是 1: {sample_dir}")
        result[role] = matches[0]
    return result


def _save_report(report: FieldAccuracyReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower().replace("¥", "￥")
    return re.sub(r"[\s,，。；：、（）()《》【】\[\]“”'\"]+", "", normalized)


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes", "是", "已确认"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
