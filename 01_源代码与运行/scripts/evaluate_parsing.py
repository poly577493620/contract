"""成对评估带水印与无水印 PDF 的解析稳定性。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean, median

from smart_compliance.comparison.normalizer import compact_text, date_values, money_values
from smart_compliance.domain.models import DocumentRole, ParsedDocument
from smart_compliance.parsing.pdf_parser import PdfParser


@dataclass(frozen=True, slots=True)
class PairMetric:
    """一对同源 PDF 的解析指标。"""

    relative_path: str
    role: str
    text_similarity: float
    money_value_stable: bool
    date_value_stable: bool
    watermarked_cleaned_characters: int
    clean_cleaned_characters: int
    watermarked_abnormal_ratio: float
    clean_abnormal_ratio: float
    watermarked_removed_noise: int
    clean_removed_noise: int


def _role(path: Path) -> DocumentRole | None:
    if path.name.endswith("_采购结果.pdf"):
        return DocumentRole.PROCUREMENT_RESULT
    if path.name.endswith("_采购合同.pdf"):
        return DocumentRole.CONTRACT
    return None


def _parse_version(root: Path, parser: PdfParser) -> dict[Path, ParsedDocument]:
    parsed: dict[Path, ParsedDocument] = {}
    for path in sorted(root.rglob("*.pdf")):
        role = _role(path)
        if role is not None:
            parsed[path.relative_to(root)] = parser.parse(path, role)
    return parsed


def _stable(left: set[object], right: set[object]) -> bool:
    if not left and not right:
        return True
    return bool(left & right)


def _pair_metric(relative_path: Path, watermarked: ParsedDocument, clean: ParsedDocument) -> PairMetric:
    watermarked_text = compact_text(watermarked.cleaned_text)
    clean_text = compact_text(clean.cleaned_text)
    similarity = SequenceMatcher(None, watermarked_text, clean_text, autojunk=False).ratio()
    watermarked_noise = (
        watermarked.quality.removed_watermark_candidates + watermarked.quality.removed_header_footer_lines
    )
    clean_noise = clean.quality.removed_watermark_candidates + clean.quality.removed_header_footer_lines
    return PairMetric(
        relative_path=relative_path.as_posix(),
        role=watermarked.role.value,
        text_similarity=round(similarity, 6),
        money_value_stable=_stable(set(money_values(watermarked_text)), set(money_values(clean_text))),
        date_value_stable=_stable(set(date_values(watermarked_text)), set(date_values(clean_text))),
        watermarked_cleaned_characters=watermarked.quality.cleaned_characters,
        clean_cleaned_characters=clean.quality.cleaned_characters,
        watermarked_abnormal_ratio=watermarked.quality.abnormal_character_ratio,
        clean_abnormal_ratio=clean.quality.abnormal_character_ratio,
        watermarked_removed_noise=watermarked_noise,
        clean_removed_noise=clean_noise,
    )


def _summary(
    metrics: list[PairMetric], watermarked: dict[Path, ParsedDocument], clean: dict[Path, ParsedDocument]
) -> dict[str, object]:
    similarities = [metric.text_similarity for metric in metrics]
    return {
        "paired_documents": len(metrics),
        "parsed_documents": len(watermarked) + len(clean),
        "parse_success_rate": 1.0,
        "mean_text_similarity": round(mean(similarities), 6),
        "median_text_similarity": round(median(similarities), 6),
        "minimum_text_similarity": round(min(similarities), 6),
        "money_value_stability_rate": round(mean(metric.money_value_stable for metric in metrics), 6),
        "date_value_stability_rate": round(mean(metric.date_value_stable for metric in metrics), 6),
        "watermarked_mean_abnormal_ratio": round(
            mean(document.quality.abnormal_character_ratio for document in watermarked.values()), 6
        ),
        "clean_mean_abnormal_ratio": round(
            mean(document.quality.abnormal_character_ratio for document in clean.values()), 6
        ),
        "watermarked_low_quality_documents": sum(
            bool(document.quality.low_quality_pages) for document in watermarked.values()
        ),
        "clean_low_quality_documents": sum(bool(document.quality.low_quality_pages) for document in clean.values()),
        "watermarked_removed_noise": sum(metric.watermarked_removed_noise for metric in metrics),
        "clean_removed_noise": sum(metric.clean_removed_noise for metric in metrics),
    }


def evaluate(dataset_root: Path, output_path: Path) -> None:
    """运行解析评估并输出 JSON。"""
    watermarked_root = dataset_root / "数据集_带水印"
    clean_root = dataset_root / "数据集_无水印"
    parser = PdfParser(max_size_mb=50, max_pages=200)
    watermarked = _parse_version(watermarked_root, parser)
    clean = _parse_version(clean_root, parser)
    if watermarked.keys() != clean.keys():
        missing = sorted(str(path) for path in watermarked.keys() ^ clean.keys())
        message = f"带水印与无水印数据集文件不成对: {missing}"
        raise ValueError(message)
    metrics = [_pair_metric(path, watermarked[path], clean[path]) for path in sorted(clean)]
    payload = {
        "dataset_root": str(dataset_root.resolve()),
        "scope": "仅采购结果与采购合同；参考答案不参与解析稳定性统计",
        "summary": _summary(metrics, watermarked, clean),
        "pairs": [asdict(metric) for metric in metrics],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.dataset_root, args.output)


if __name__ == "__main__":
    main()
