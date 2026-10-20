"""JSON 报告导出。"""

from pathlib import Path

from smart_compliance.domain.models import ReviewReport
from smart_compliance.reporting.audit import public_report


def export_json(report: ReviewReport, output_path: Path) -> Path:
    """导出完整 JSON 报告。

    Args:
        report: 质检报告。
        output_path: 输出路径。

    Returns:
        已写入文件路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(public_report(report).model_dump_json(indent=2), encoding="utf-8")
    return output_path
