"""至少三模型、可容错、可复现的受控评测执行器。"""

import csv
import hashlib
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from smart_compliance.comparison.comparator import LlmComparator
from smart_compliance.comparison.prompt_builder import PROMPT_VERSION
from smart_compliance.config import Settings, portable_project_path
from smart_compliance.domain.exceptions import (
    ModelInvocationError,
    ModelOutputError,
    OcrQualityError,
    SmartComplianceError,
)
from smart_compliance.domain.models import DocumentRole, GoldIssue, ParsedDocument, PredictionIssue, Verdict
from smart_compliance.evaluation.data_split import validate_gold_sample
from smart_compliance.evaluation.gold_loader import load_verified_gold
from smart_compliance.evaluation.matcher import metrics_for, prediction_issues
from smart_compliance.evaluation.models import EvaluationReport, ModelEvaluation, SampleRun
from smart_compliance.llm.base import LlmProvider
from smart_compliance.llm.registry import ProviderRegistry
from smart_compliance.parsing.pdf_parser import PdfParser

IssueKey = tuple[str, Verdict, str]


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """一次可复现评测的输入参数。"""

    dataset_version: Path
    sample_ids: list[str]
    model_names: list[str]
    gold_path: Path
    output_dir: Path
    repetitions: int = 3


@dataclass(frozen=True, slots=True)
class _SampleExecution:
    documents: tuple[ParsedDocument, ParsedDocument]
    sample_id: str
    model_name: str
    repetition: int
    raw_path: Path


@dataclass(frozen=True, slots=True)
class _FailedRunContext:
    provider: LlmProvider
    sample_id: str
    model_name: str
    repetition: int
    raw_path: Path
    ocr_hashes: tuple[str | None, str | None] = (None, None)


class EvaluationRunner:
    """在相同输入和提示词下交错运行多个模型。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化评测器。"""
        self._settings = settings or Settings()
        self._parser = PdfParser.from_settings(self._settings)
        self._registry = ProviderRegistry(self._settings)

    async def run(self, request: EvaluationRequest) -> EvaluationReport:
        """执行评测；单模型或单样本失败只记录，不终止整批。"""
        dataset_version = request.dataset_version
        sample_ids = request.sample_ids
        model_names = request.model_names
        gold_path = request.gold_path
        output_dir = request.output_dir
        repetitions = request.repetitions
        if not sample_ids:
            raise ValueError("评测样本不能为空。")
        if len(set(model_names)) < 3:
            raise ValueError("多模型评测至少需要 3 个不同模型端点。")
        if repetitions < 1:
            raise ValueError("重复次数至少为 1。")
        for sample_id in sample_ids:
            validate_gold_sample(sample_id)
        gold = [issue for issue in load_verified_gold(gold_path) if issue.sample_id in sample_ids]
        files = {sample_id: _sample_files(dataset_version / sample_id) for sample_id in sample_ids}
        pdf_input_hashes = {
            sample_id: {
                "procurement_result_pdf": _sha256(paths[0]),
                "contract_pdf": _sha256(paths[1]),
            }
            for sample_id, paths in files.items()
        }
        documents, parsing_errors = self._parse_samples(files)
        input_hashes = _evaluation_input_hashes(pdf_input_hashes, documents)
        ocr_inputs = _ocr_input_manifest(documents, parsing_errors)
        output_dir.mkdir(parents=True, exist_ok=True)
        providers = {name: self._registry.get(name) for name in model_names}
        comparators = {name: LlmComparator(provider, settings=self._settings) for name, provider in providers.items()}
        runs: list[SampleRun] = []
        raw_predictions: dict[str, list[PredictionIssue]] = {name: [] for name in model_names}
        for repetition in range(1, repetitions + 1):
            for sample_id in sample_ids:
                for model_name in model_names:
                    raw_path = output_dir / "raw" / model_name / f"{sample_id}-r{repetition}.txt"
                    selected = documents.get(sample_id)
                    ocr_hashes = (
                        (selected[0].ocr_text_sha256, selected[1].ocr_text_sha256) if selected else (None, None)
                    )
                    context = _FailedRunContext(
                        providers[model_name], sample_id, model_name, repetition, raw_path, ocr_hashes
                    )
                    try:
                        if sample_id in parsing_errors:
                            raise parsing_errors[sample_id]
                        execution = _SampleExecution(
                            documents=documents[sample_id],
                            sample_id=sample_id,
                            model_name=model_name,
                            repetition=repetition,
                            raw_path=raw_path,
                        )
                        run, predictions = await self._run_sample(execution, comparators[model_name])
                    except ModelInvocationError as error:
                        run = _failed_run(context, error)
                        predictions = []
                    except ModelOutputError as error:
                        run = _failed_run(context, error)
                        predictions = []
                    except (SmartComplianceError, ValueError, OSError) as error:
                        run = _failed_run(context, error)
                        predictions = []
                    runs.append(run)
                    raw_predictions[model_name].extend(predictions)
        predictions_by_model = {
            name: _consensus_predictions(raw_predictions[name], repetitions) for name in model_names
        }
        evaluations = [
            self._aggregate(name, predictions_by_model[name], gold, runs, sample_ids) for name in model_names
        ]
        report = EvaluationReport(
            prompt_version=PROMPT_VERSION,
            sample_ids=sample_ids,
            repetitions=repetitions,
            input_hashes=input_hashes,
            ocr_inputs=ocr_inputs,
            model_parameters=_model_parameters(self._settings, model_names),
            models=evaluations,
            runs=runs,
            caveats=[
                "Gold 仅来自 001-020 且必须人工核验；021-030 未参与带标签指标计算。",
                "重复运行使用多数票形成模型问题集；失败运行按未命中处理并单独计入失败率。",
                "参考答案外但有原文依据的预测进入人工仲裁表，自动指标不能替代案例复核。",
            ],
        )
        write_evaluation_outputs(report, output_dir, predictions_by_model, gold)
        return report

    async def _run_sample(
        self,
        execution: _SampleExecution,
        comparator: LlmComparator,
    ) -> tuple[SampleRun, list[PredictionIssue]]:
        procurement, contract = execution.documents
        report = await comparator.compare(procurement, contract, execution.raw_path)
        return (
            SampleRun(
                model_name=execution.model_name,
                sample_id=execution.sample_id,
                success=True,
                repetition=execution.repetition,
                provider=report.model.provider,
                exact_model_name=report.model.model_name,
                prompt_version=report.model.prompt_version,
                started_at=report.model.invoked_at,
                latency_ms=report.model.latency_ms,
                input_tokens=report.model.input_tokens,
                output_tokens=report.model.output_tokens,
                estimated_cost=report.model.estimated_cost,
                retry_count=report.model.retry_count,
                finish_reason=report.model.finish_reason,
                raw_output_file=portable_project_path(execution.raw_path, root=self._settings.root),
                ocr_source="page_image",
                procurement_ocr_text_sha256=procurement.ocr_text_sha256,
                contract_ocr_text_sha256=contract.ocr_text_sha256,
            ),
            _deduplicate_predictions(prediction_issues(report)),
        )

    def _parse_samples(
        self,
        files: dict[str, tuple[Path, Path]],
    ) -> tuple[dict[str, tuple[ParsedDocument, ParsedDocument]], dict[str, SmartComplianceError]]:
        documents: dict[str, tuple[ParsedDocument, ParsedDocument]] = {}
        errors: dict[str, SmartComplianceError] = {}
        for sample_id, paths in files.items():
            try:
                procurement = self._parser.parse(paths[0], DocumentRole.PROCUREMENT_RESULT)
                contract = self._parser.parse(paths[1], DocumentRole.CONTRACT)
            except SmartComplianceError as error:
                errors[sample_id] = error
                continue
            documents[sample_id] = (procurement, contract)
        return documents, errors

    @staticmethod
    def _aggregate(
        model_name: str,
        predictions: list[PredictionIssue],
        gold: list[GoldIssue],
        runs: list[SampleRun],
        sample_ids: list[str],
    ) -> ModelEvaluation:
        model_runs = [run for run in runs if run.model_name == model_name]
        latencies = sorted(run.latency_ms for run in model_runs if run.latency_ms is not None)
        costs = [run.estimated_cost for run in model_runs if run.estimated_cost is not None]
        sample_metrics = [
            metrics_for(
                [prediction for prediction in predictions if prediction.sample_id == sample_id],
                [issue for issue in gold if issue.sample_id == sample_id],
            )
            for sample_id in sample_ids
        ]
        successful = sum(run.success for run in model_runs)
        failed = len(model_runs) - successful
        return ModelEvaluation(
            model_name=model_name,
            issue_metrics=metrics_for(predictions, gold),
            missing_metrics=metrics_for(predictions, gold, Verdict.MISSING),
            different_metrics=metrics_for(predictions, gold, Verdict.DIFFERENT),
            macro_precision=statistics.fmean(metric.precision for metric in sample_metrics),
            macro_recall=statistics.fmean(metric.recall for metric in sample_metrics),
            macro_f1=statistics.fmean(metric.f1 for metric in sample_metrics),
            median_latency_ms=statistics.median(latencies) if latencies else None,
            p95_latency_ms=_percentile(latencies, 0.95),
            total_cost=sum(costs) if costs else None,
            successful_runs=successful,
            failed_runs=failed,
            failure_rate=failed / len(model_runs) if model_runs else 0,
        )


def write_evaluation_outputs(
    report: EvaluationReport,
    output_dir: Path,
    predictions_by_model: dict[str, list[PredictionIssue]],
    gold: list[GoldIssue],
) -> tuple[Path, Path, Path, Path, Path]:
    """输出 JSON、模型汇总 CSV、运行 CSV、Markdown 和仲裁 CSV。"""
    json_path = output_dir / "model_evaluation.json"
    csv_path = output_dir / "model_evaluation.csv"
    runs_path = output_dir / "runs.csv"
    markdown_path = output_dir / "model_evaluation.md"
    arbitration_path = output_dir / "human-arbitration.csv"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    _write_model_csv(report, csv_path)
    _write_runs_csv(report.runs, runs_path)
    _write_markdown(report, markdown_path)
    _write_arbitration(predictions_by_model, gold, arbitration_path)
    (output_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "prompt_version": report.prompt_version,
                "sample_ids": report.sample_ids,
                "repetitions": report.repetitions,
                "input_hashes": report.input_hashes,
                "ocr_inputs": report.ocr_inputs,
                "model_parameters": report.model_parameters,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return json_path, csv_path, runs_path, markdown_path, arbitration_path


def _failed_run(
    context: _FailedRunContext,
    error: Exception,
) -> SampleRun:
    if isinstance(error, ModelInvocationError):
        category = error.category
    elif isinstance(error, OcrQualityError):
        category = "ocr_quality_failed"
    else:
        category = type(error).__name__
    retry_count = error.retry_count if isinstance(error, ModelInvocationError | ModelOutputError) else 0
    elapsed_ms = error.elapsed_ms if isinstance(error, ModelInvocationError | ModelOutputError) else None
    return SampleRun(
        model_name=context.model_name,
        sample_id=context.sample_id,
        success=False,
        repetition=context.repetition,
        provider=context.provider.provider_name,
        exact_model_name=context.provider.model_name,
        prompt_version=PROMPT_VERSION,
        latency_ms=elapsed_ms,
        retry_count=retry_count,
        raw_output_file=portable_project_path(context.raw_path) if context.raw_path.exists() else None,
        error_category=category,
        http_status=error.status_code if isinstance(error, ModelInvocationError) else None,
        error=str(error),
        ocr_source="page_image" if all(context.ocr_hashes) else None,
        procurement_ocr_text_sha256=context.ocr_hashes[0],
        contract_ocr_text_sha256=context.ocr_hashes[1],
    )


def _consensus_predictions(predictions: list[PredictionIssue], repetitions: int) -> list[PredictionIssue]:
    counts: Counter[IssueKey] = Counter(
        (prediction.sample_id, prediction.issue_type, prediction.clause_code) for prediction in predictions
    )
    representatives = {
        (prediction.sample_id, prediction.issue_type, prediction.clause_code): prediction for prediction in predictions
    }
    threshold = repetitions // 2 + 1
    return [representatives[key] for key, count in counts.items() if count >= threshold]


def _deduplicate_predictions(predictions: list[PredictionIssue]) -> list[PredictionIssue]:
    unique: dict[IssueKey, PredictionIssue] = {}
    for prediction in predictions:
        key = (prediction.sample_id, prediction.issue_type, prediction.clause_code)
        unique.setdefault(key, prediction)
    return list(unique.values())


def _model_parameters(
    settings: Settings,
    model_names: list[str],
) -> dict[str, dict[str, str | int | float | bool | None]]:
    selected = {endpoint.name: endpoint for endpoint in settings.endpoints if endpoint.name in model_names}
    return {
        name: {
            "provider": endpoint.provider,
            "model": endpoint.model,
            "api_path": endpoint.api_path,
            "temperature": endpoint.request_temperature,
            "response_format": endpoint.response_format,
            "max_output_tokens": endpoint.max_output_tokens,
            "timeout_seconds": endpoint.timeout_seconds or settings.request_timeout_seconds,
            "max_retries": endpoint.max_retries if endpoint.max_retries is not None else settings.max_retries,
            "thinking": endpoint.request_thinking or "none",
            "reasoning_effort": endpoint.request_reasoning_effort,
            "price_input_per_million": endpoint.price_input_per_million,
            "price_output_per_million": endpoint.price_output_per_million,
            "price_currency": endpoint.price_currency,
            "price_unit": endpoint.price_unit,
            "price_source_url": endpoint.price_source_url,
            "price_verified_at": endpoint.price_verified_at.isoformat() if endpoint.price_verified_at else None,
        }
        for name, endpoint in selected.items()
    }


def _write_model_csv(report: EvaluationReport, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "model",
                "micro_precision",
                "micro_recall",
                "micro_f1",
                "macro_precision",
                "macro_recall",
                "macro_f1",
                "median_ms",
                "p95_ms",
                "cost",
                "success",
                "failed",
                "failure_rate",
            ]
        )
        for model in report.models:
            writer.writerow(
                [
                    model.model_name,
                    model.issue_metrics.precision,
                    model.issue_metrics.recall,
                    model.issue_metrics.f1,
                    model.macro_precision,
                    model.macro_recall,
                    model.macro_f1,
                    model.median_latency_ms,
                    model.p95_latency_ms,
                    model.total_cost,
                    model.successful_runs,
                    model.failed_runs,
                    model.failure_rate,
                ]
            )


def _write_runs_csv(runs: list[SampleRun], path: Path) -> None:
    fields = list(SampleRun.model_fields)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            writer.writerow(run.model_dump(mode="json"))


def _write_markdown(report: EvaluationReport, path: Path) -> None:
    lines = [
        "# 多模型对比评测",
        "",
        f"提示词版本：`{report.prompt_version}`；每模型每样本重复：{report.repetitions} 次。",
        "",
        "| 模型 | Micro F1 | Macro F1 | 失败率 | 中位耗时(ms) | P95(ms) | 成本 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model in report.models:
        lines.append(
            f"| {model.model_name} | {model.issue_metrics.f1:.3f} | {model.macro_f1:.3f} | "
            f"{model.failure_rate:.1%} | {model.median_latency_ms or '未知'} | {model.p95_latency_ms or '未知'} | "
            f"{model.total_cost if model.total_cost is not None else '未知'} |"
        )
    lines.extend(["", "## 口径与限制", ""] + [f"- {caveat}" for caveat in report.caveats])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_arbitration(
    predictions_by_model: dict[str, list[PredictionIssue]],
    gold: list[GoldIssue],
    path: Path,
) -> None:
    gold_keys = {(issue.sample_id, issue.issue_type, issue.clause_code) for issue in gold}
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["model", "sample_id", "issue_type", "clause_code", "disagreement", "human_decision"])
        for model_name, predictions in predictions_by_model.items():
            prediction_keys = {
                (prediction.sample_id, prediction.issue_type, prediction.clause_code) for prediction in predictions
            }
            for key in sorted(prediction_keys - gold_keys):
                writer.writerow([model_name, key[0], key[1].value, key[2], "prediction_only", "待人工仲裁"])
            for key in sorted(gold_keys - prediction_keys):
                writer.writerow([model_name, key[0], key[1].value, key[2], "gold_only", "待人工仲裁"])


def _sample_files(sample_dir: Path) -> tuple[Path, Path]:
    result_files = list(sample_dir.glob("*采购结果.pdf"))
    contract_files = list(sample_dir.glob("*采购合同.pdf"))
    if len(result_files) != 1 or len(contract_files) != 1:
        raise ValueError(f"样本目录文件不完整: {sample_dir}")
    return result_files[0], contract_files[0]


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, round((len(values) - 1) * percentile)))
    return float(values[index])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _evaluation_input_hashes(
    pdf_hashes: dict[str, dict[str, str]],
    documents: dict[str, tuple[ParsedDocument, ParsedDocument]],
) -> dict[str, dict[str, str]]:
    result = {sample_id: dict(hashes) for sample_id, hashes in pdf_hashes.items()}
    for sample_id, (procurement, contract) in documents.items():
        if procurement.ocr_text_sha256:
            result[sample_id]["procurement_result_ocr_text"] = procurement.ocr_text_sha256
        if contract.ocr_text_sha256:
            result[sample_id]["contract_ocr_text"] = contract.ocr_text_sha256
    return result


def _ocr_input_manifest(
    documents: dict[str, tuple[ParsedDocument, ParsedDocument]],
    errors: dict[str, SmartComplianceError],
) -> dict[str, dict[str, object]]:
    manifest: dict[str, dict[str, object]] = {}
    for sample_id, (procurement, contract) in documents.items():
        manifest[sample_id] = {
            "ocr_source": "page_image",
            "procurement_result": _ocr_document_manifest(procurement),
            "contract": _ocr_document_manifest(contract),
            "same_input_for_all_models": True,
        }
    for sample_id, error in errors.items():
        manifest[sample_id] = {"ocr_source": "page_image", "status": "failed", "error": str(error)}
    return manifest


def _ocr_document_manifest(document: ParsedDocument) -> dict[str, object]:
    metadata = document.ocr_metadata
    return {
        "pdf_sha256": document.sha256,
        "ocr_text_sha256": document.ocr_text_sha256,
        "dpi": metadata.dpi if metadata else None,
        "attempted_dpis": metadata.attempted_dpis if metadata else [],
        "cache_key": metadata.cache_key if metadata else None,
        "quality_gate_passed": metadata.quality_gate_passed if metadata else False,
    }
