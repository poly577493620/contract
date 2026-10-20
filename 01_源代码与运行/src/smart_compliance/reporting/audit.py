"""报告公开审计字段的兼容与脱敏。"""

from smart_compliance.config import portable_project_path
from smart_compliance.domain.models import ReviewReport


def public_raw_output_file(report: ReviewReport) -> str | None:
    """返回不含本机绝对目录的原始输出位置。"""
    value = report.model.raw_output_file
    return portable_project_path(value) if value else None


def public_report(report: ReviewReport) -> ReviewReport:
    """创建适合导出的报告副本，保留原始内存对象不变。"""
    raw_output_file = public_raw_output_file(report)
    if raw_output_file == report.model.raw_output_file:
        return report
    model = report.model.model_copy(update={"raw_output_file": raw_output_file})
    return report.model_copy(update={"model": model})


def source_hash(report: ReviewReport, key: str) -> str:
    """读取当前哈希键，并兼容第一阶段生成的旧键名。"""
    legacy_keys = {
        "procurement_result_pdf": "procurement_result",
        "contract_pdf": "contract",
    }
    return report.source_hashes.get(key) or report.source_hashes.get(legacy_keys.get(key, ""), "未知")
