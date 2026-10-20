"""智审合规助手命令行入口。"""

import asyncio
import json
import logging
from pathlib import Path
from typing import cast

import click
import structlog

from smart_compliance.comparison.comparator import LlmComparator, RuleBasedComparator
from smart_compliance.config import Settings
from smart_compliance.domain.exceptions import SmartComplianceError
from smart_compliance.domain.models import DocumentRole, ReviewReport
from smart_compliance.evaluation.data_split import parse_training_samples
from smart_compliance.evaluation.field_accuracy import (
    FieldEvaluationRequest,
    build_annotation_template,
    build_blind_manifest,
    evaluate_field_annotations,
)
from smart_compliance.evaluation.gold_loader import (
    build_gold_candidates,
    save_gold_candidates,
    save_gold_manifest,
    save_gold_review_sheet,
    validate_gold_file,
)
from smart_compliance.evaluation.runner import EvaluationRequest, EvaluationRunner
from smart_compliance.llm.registry import ProviderRegistry
from smart_compliance.parsing.pdf_parser import PdfParser
from smart_compliance.reporting import export_docx, export_json, export_pdf

log = structlog.get_logger()


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


@click.group()
@click.version_option()
def main() -> None:
    """智审合规助手：PDF 解析、合同质检、报告导出与多模型评测。"""
    _configure_logging(Settings().log_level)


@main.command("parse")
@click.option("--input", "input_path", required=True, type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--role",
    type=click.Choice([role.value for role in DocumentRole], case_sensitive=False),
    default=DocumentRole.PROCUREMENT_RESULT.value,
    show_default=True,
)
@click.option("--output", "output_path", required=True, type=click.Path(path_type=Path, dir_okay=False))
def parse_command(input_path: Path, role: str, output_path: Path) -> None:
    """解析一份 PDF 并输出可审计 JSON。"""
    settings = Settings()
    try:
        destination = settings.writable_path(output_path)
        parser = PdfParser.from_settings(settings)
        document = parser.parse(input_path, DocumentRole(role))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({"output": str(destination), "quality": document.quality.model_dump()}, ensure_ascii=False))


@main.command("review")
@click.option("--result", "result_path", required=True, type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--contract", "contract_path", required=True, type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option("--model", default="rules", show_default=True, help="rules 或 .env 中配置的模型端点名称")
@click.option(
    "--output", "output_dir", default=Path("outputs/reviews"), type=click.Path(path_type=Path, file_okay=False)
)
def review_command(result_path: Path, contract_path: Path, model: str, output_dir: Path) -> None:
    """比对采购结果和合同，导出 JSON/DOCX/PDF。"""
    settings = Settings()
    try:
        destination = settings.writable_path(output_dir)
        report = _build_review(settings, result_path, contract_path, model, destination)
        outputs = _export_review(report, destination)
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({name: str(path) for name, path in outputs.items()}, ensure_ascii=False))


@main.command("build-gold")
@click.option(
    "--dataset-version",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option("--output", "output_path", default=Path("data/gold/candidates.json"), type=click.Path(path_type=Path))
def build_gold_command(dataset_version: Path, output_path: Path) -> None:
    """从 001-020 参考答案生成待人工核验的 gold 候选。"""
    settings = Settings()
    try:
        destination = settings.writable_path(output_path)
        references = [
            path for sample in range(1, 21) for path in (dataset_version / f"{sample:03d}").glob("*参考答案.pdf")
        ]
        parser = PdfParser.from_settings(settings)
        issues = build_gold_candidates(references, parser)
        save_gold_candidates(issues, destination)
        review_sheet = save_gold_review_sheet(issues, destination.with_name("review-sheet.csv"))
        manifest = save_gold_manifest(references, issues, destination.with_name("gold-manifest.json"))
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            {
                "output": str(destination),
                "candidate_count": len(issues),
                "review_sheet": str(review_sheet),
                "manifest": str(manifest),
            },
            ensure_ascii=False,
        )
    )


@main.command("validate-gold")
@click.option("--gold", "gold_path", required=True, type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--manifest", "manifest_path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def validate_gold_command(gold_path: Path, manifest_path: Path | None) -> None:
    """检查 Gold 结构、001-020 覆盖和人工核验状态。"""
    settings = Settings()
    try:
        settings.writable_path(gold_path)
        selected_manifest = manifest_path or gold_path.with_name("gold-manifest.json")
        report = validate_gold_file(gold_path, selected_manifest)
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(report.model_dump_json(indent=2))


@main.command("build-field-template")
@click.option(
    "--dataset-version",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option("--samples", default="001-020", show_default=True)
@click.option(
    "--output",
    "output_path",
    default=Path("data/annotations/field-annotations.csv"),
    type=click.Path(path_type=Path, dir_okay=False),
)
def build_field_template_command(dataset_version: Path, samples: str, output_path: Path) -> None:
    """生成供应商、金额、数量等字段的人工标注模板。"""
    settings = Settings()
    try:
        destination = settings.writable_path(output_path)
        build_annotation_template(dataset_version, parse_training_samples(samples), destination)
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({"output": str(destination), "status": "待人工标注"}, ensure_ascii=False))


@main.command("evaluate-fields")
@click.option(
    "--dataset-version",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--annotations",
    "annotations_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--output",
    "output_path",
    default=Path("outputs/evaluations/field-accuracy.json"),
    type=click.Path(path_type=Path, dir_okay=False),
)
def evaluate_fields_command(dataset_version: Path, annotations_path: Path, output_path: Path) -> None:
    """仅对人工核验字段计算解析命中率；无标注时返回待标注。"""
    settings = Settings()
    try:
        destination = settings.writable_path(output_path)
        report = evaluate_field_annotations(
            FieldEvaluationRequest(
                dataset_version=dataset_version,
                annotations_path=annotations_path,
                output_path=destination,
                max_upload_mb=settings.max_upload_mb,
                max_pdf_pages=settings.max_pdf_pages,
            )
        )
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(report.model_dump_json(indent=2))


@main.command("build-blind-manifest")
@click.option(
    "--dataset-version",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--output",
    "output_path",
    default=Path("outputs/blind/blind-manifest.json"),
    type=click.Path(path_type=Path, dir_okay=False),
)
def build_blind_manifest_command(dataset_version: Path, output_path: Path) -> None:
    """生成 021-030 无标签输入哈希清单，不计算准确率。"""
    settings = Settings()
    try:
        destination = settings.writable_path(output_path)
        build_blind_manifest(dataset_version, destination)
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({"output": str(destination), "labeled": False, "metrics": None}, ensure_ascii=False))


@main.command("evaluate")
@click.option(
    "--dataset-version",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option("--samples", default="001-020", show_default=True)
@click.option("--models", required=True, help="至少三个已配置端点名称，以逗号分隔")
@click.option("--gold", "gold_path", required=True, type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--output", "output_dir", default=Path("outputs/evaluations"), type=click.Path(path_type=Path))
@click.option("--repetitions", default=3, show_default=True, type=click.IntRange(min=1, max=10))
def evaluate_command(**options: object) -> None:
    """在相同输入和提示词下执行至少三模型评测。"""
    settings = Settings()
    try:
        dataset_version = cast(Path, options["dataset_version"])
        samples = cast(str, options["samples"])
        models = cast(str, options["models"])
        gold_path = cast(Path, options["gold_path"])
        output_dir = cast(Path, options["output_dir"])
        repetitions = cast(int, options["repetitions"])
        destination = settings.writable_path(output_dir)
        sample_ids = parse_training_samples(samples)
        model_names = [name.strip() for name in models.split(",") if name.strip()]
        report = asyncio.run(
            EvaluationRunner(settings).run(
                EvaluationRequest(
                    dataset_version=dataset_version,
                    sample_ids=sample_ids,
                    model_names=model_names,
                    gold_path=gold_path,
                    output_dir=destination,
                    repetitions=repetitions,
                )
            )
        )
    except (SmartComplianceError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            {"output": str(destination), "models": [model.model_name for model in report.models]}, ensure_ascii=False
        )
    )


def _build_review(
    settings: Settings,
    result_path: Path,
    contract_path: Path,
    model: str,
    output_dir: Path,
) -> ReviewReport:
    parser = PdfParser.from_settings(settings)
    procurement = parser.parse(result_path, DocumentRole.PROCUREMENT_RESULT)
    contract = parser.parse(contract_path, DocumentRole.CONTRACT)
    if model == "rules":
        return RuleBasedComparator(settings).compare(procurement, contract)
    provider = ProviderRegistry(settings).get(model)
    raw_path = output_dir / "raw" / f"{procurement.sha256[:12]}-{model}.txt"
    return asyncio.run(LlmComparator(provider, settings=settings).compare(procurement, contract, raw_path))


def _export_review(report: ReviewReport, output_dir: Path) -> dict[str, Path]:
    prefix = f"review-{report.sample_id or report.report_id[:8]}"
    return {
        "json": export_json(report, output_dir / f"{prefix}.json"),
        "docx": export_docx(report, output_dir / f"{prefix}.docx"),
        "pdf": export_pdf(report, output_dir / f"{prefix}.pdf"),
    }
