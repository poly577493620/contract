"""生成适合中文阅读的 PDF 质检报告。"""

from functools import partial
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from smart_compliance.domain.models import Evidence, ReviewItem, ReviewReport, Verdict
from smart_compliance.reporting.audit import public_raw_output_file, source_hash

EMBEDDED_FONT_NAME = "SmartComplianceCJK"
FALLBACK_FONT_NAME = "STSong-Light"
WINDOWS_FONT_PATH = Path("C:/Windows/Fonts/simhei.ttf")


def export_pdf(report: ReviewReport, output_path: Path) -> Path:
    """导出 PDF 质检报告。

    Args:
        report: 质检报告。
        output_path: 输出路径。

    Returns:
        已生成文件路径。
    """
    font_name = _register_font()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
        title="采购合同智能质检报告",
        author="智审合规助手",
    )
    styles = _styles(font_name)
    story: list[Any] = []
    story.extend(_masthead(report, styles))
    story.extend(_summary(report, styles, font_name))
    story.append(Paragraph("二、逐条质检结果", styles["Heading1CN"]))
    for item in report.items:
        story.append(KeepTogether(_item_flowables(item, styles)))
    story.extend(_quality(report, styles, font_name))
    story.extend(_audit(report, styles))
    story.extend(_limitations(report, styles))
    page_furniture = partial(_page_furniture, font_name=font_name)
    document.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)
    return output_path


def _register_font() -> str:
    if WINDOWS_FONT_PATH.is_file():
        if EMBEDDED_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(EMBEDDED_FONT_NAME, str(WINDOWS_FONT_PATH)))
        return EMBEDDED_FONT_NAME
    if FALLBACK_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FALLBACK_FONT_NAME))
    return FALLBACK_FONT_NAME


def _styles(font_name: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "TitleCN": ParagraphStyle(
            "TitleCN",
            parent=base["Title"],
            fontName=font_name,
            fontSize=22,
            leading=28,
            textColor=colors.HexColor("#222222"),
        ),
        "SubtitleCN": ParagraphStyle(
            "SubtitleCN",
            parent=base["Normal"],
            fontName=font_name,
            fontSize=12,
            leading=18,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#555555"),
        ),
        "Heading1CN": ParagraphStyle(
            "Heading1CN",
            parent=base["Heading1"],
            fontName=font_name,
            fontSize=15,
            leading=20,
            spaceBefore=12,
            spaceAfter=6,
            textColor=colors.HexColor("#2E74B5"),
        ),
        "Heading2CN": ParagraphStyle(
            "Heading2CN",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=12,
            leading=17,
            spaceBefore=8,
            spaceAfter=4,
            textColor=colors.HexColor("#1F4D78"),
        ),
        "BodyCN": ParagraphStyle(
            "BodyCN",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9,
            leading=12,
            spaceAfter=3,
            alignment=TA_LEFT,
        ),
        "SmallCN": ParagraphStyle(
            "SmallCN",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#555555"),
        ),
    }


def _masthead(report: ReviewReport, styles: dict[str, ParagraphStyle]) -> list[Any]:
    items: list[Any] = [
        Paragraph("采购合同智能质检报告", styles["TitleCN"]),
        Paragraph(_escape(report.project_name), styles["SubtitleCN"]),
        Spacer(1, 14),
    ]
    metadata = (
        f"报告编号：{report.report_id}<br/>样本编号：{report.sample_id or '未识别'}<br/>"
        f"生成时间：{report.generated_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}<br/>"
        f"质检引擎：{report.model.provider} / {report.model.model_name}<br/>"
        f"提示词版本：{report.model.prompt_version}<br/>运行 ID：{report.model.run_id}"
    )
    items.append(Paragraph(metadata, styles["BodyCN"]))
    items.append(Paragraph(f"<b>重要提示：</b>{_escape(report.disclaimer)}", styles["BodyCN"]))
    return items


def _summary(report: ReviewReport, styles: dict[str, ParagraphStyle], font_name: str) -> list[Any]:
    second_label = "覆盖编码" if report.summary.covered_clause_count is not None else "一致项"
    second_value = (
        report.summary.covered_clause_count
        if report.summary.covered_clause_count is not None
        else report.summary.consistent_count
    )
    data: list[list[Any]] = [
        ["总体风险", second_label, "缺失项", "差异项"],
        [
            report.summary.overall_risk.value.upper(),
            second_value,
            report.summary.missing_count,
            report.summary.different_count,
        ],
    ]
    table = Table(data, colWidths=[1.625 * inch] * 4, repeatRows=1)
    table.setStyle(_table_style(font_name))
    return [Paragraph("一、质检摘要", styles["Heading1CN"]), table, Spacer(1, 8)]


def _item_flowables(item: ReviewItem, styles: dict[str, ParagraphStyle]) -> list[Any]:
    status = {Verdict.CONSISTENT: "一致项", Verdict.MISSING: "缺失项", Verdict.DIFFERENT: "差异项"}[item.verdict]
    contract = _evidence(item.contract_evidence) or "未找到对应证据"
    return [
        Paragraph(f"{item.source_order}. {_escape(item.clause_name)} [{status}]", styles["Heading2CN"]),
        Paragraph(f"<b>采购结果证据：</b>{_escape(_evidence(item.procurement_evidence))}", styles["BodyCN"]),
        Paragraph(f"<b>合同证据：</b>{_escape(contract)}", styles["BodyCN"]),
        Paragraph(f"<b>判断：</b>{_escape(item.difference)}", styles["BodyCN"]),
        Paragraph(f"<b>风险说明：</b>{_escape(item.risk_reason)}", styles["BodyCN"]),
        Paragraph(f"<b>复核建议：</b>{_escape(item.recommendation)}", styles["BodyCN"]),
        Paragraph(
            f"<b>可信度/复核：</b>{item.confidence:.0%}；"
            f"{'需要人工复核' if item.needs_human_review else '建议抽样复核'}",
            styles["BodyCN"],
        ),
    ]


def _quality(report: ReviewReport, styles: dict[str, ParagraphStyle], font_name: str) -> list[Any]:
    data: list[list[object]] = [["文档", "页数", "原始字符", "清洗字符", "移除水印", "低质量页"]]
    for role, quality in report.parsing_quality.items():
        data.append(
            [
                "采购结果" if role == "procurement_result" else "采购合同",
                quality.page_count,
                quality.raw_characters,
                quality.cleaned_characters,
                quality.removed_watermark_candidates,
                "、".join(str(page) for page in quality.low_quality_pages) or "无",
            ]
        )
    table = Table(
        data, colWidths=[1.3 * inch, 0.7 * inch, 1.05 * inch, 1.05 * inch, 1.05 * inch, 1.35 * inch], repeatRows=1
    )
    table.setStyle(_table_style(font_name))
    return [Paragraph("三、PDF 解析质量", styles["Heading1CN"]), table]


def _limitations(report: ReviewReport, styles: dict[str, ParagraphStyle]) -> list[Any]:
    flowables: list[Any] = [Paragraph("五、局限与人工复核", styles["Heading1CN"])]
    for index, limitation in enumerate(report.limitations, start=1):
        flowables.append(Paragraph(f"<b>局限 {index}：</b>{_escape(limitation)}", styles["BodyCN"]))
    flowables.append(
        Paragraph("<b>最终结论：</b>请由采购、业务与法务人员结合原始文件完成复核和签章。", styles["BodyCN"])
    )
    return flowables


def _audit(report: ReviewReport, styles: dict[str, ParagraphStyle]) -> list[Any]:
    result_type = "外部模型辅助结果" if report.model.raw_output_file else "离线规则基线结果"
    raw_output_file = public_raw_output_file(report)
    values = (
        f"<b>结果类型：</b>{result_type}",
        f"<b>证据校验：</b>{report.evidence_validation_status.value}",
        f"<b>人工复核：</b>{'需要' if report.summary.needs_human_review else '建议抽样复核'}",
        f"<b>采购结果 PDF SHA-256：</b>{source_hash(report, 'procurement_result_pdf')}",
        f"<b>采购合同 PDF SHA-256：</b>{source_hash(report, 'contract_pdf')}",
        f"<b>采购结果 OCR 文本 SHA-256：</b>{source_hash(report, 'procurement_result_ocr_text')}",
        f"<b>采购合同 OCR 文本 SHA-256：</b>{source_hash(report, 'contract_ocr_text')}",
        f"<b>原始模型输出：</b>{_escape(raw_output_file or '不适用（离线规则未调用 LLM）')}",
        f"<b>API 路径/重试：</b>{_escape(report.model.api_path or '不适用')} / {report.model.retry_count}",
    )
    flowables: list[Any] = [Paragraph("四、运行与证据审计", styles["Heading1CN"])]
    flowables.extend(Paragraph(value, styles["SmallCN"]) for value in values)
    warnings = report.validation_warnings or ["未记录证据校验警告。"]
    for index, warning in enumerate(warnings, start=1):
        flowables.append(Paragraph(f"<b>校验摘要 {index}：</b>{_escape(warning)}", styles["SmallCN"]))
    return flowables


def _table_style(font_name: str) -> TableStyle:
    return TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#222222")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B5B5B5")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
    )


def _page_furniture(canvas: Canvas, document: SimpleDocTemplate, *, font_name: str) -> None:
    canvas.saveState()
    canvas.setFont(font_name, 8)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(inch, 10.45 * inch, "智审合规助手 | 采购合同质检报告")
    canvas.drawRightString(7.5 * inch, 0.55 * inch, f"第 {document.page} 页 | 教学模拟/测试数据 | 须人工复核")
    canvas.restoreState()


def _evidence(evidence: Evidence) -> str:
    if not evidence.quote:
        return ""
    location = f"第 {evidence.page} 页 / {evidence.chunk_id}" if evidence.page else "页码未知"
    return f"{evidence.quote}（{location}）"


def _escape(text: str) -> str:
    return text.replace("¥", "￥").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
