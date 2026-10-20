"""智审合规助手 Streamlit 原型。"""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import streamlit as st

from smart_compliance.comparison.comparator import LlmComparator, RuleBasedComparator
from smart_compliance.config import ModelEndpoint, Settings
from smart_compliance.domain.exceptions import SmartComplianceError
from smart_compliance.domain.models import (
    Dimension,
    DocumentRole,
    ParsedDocument,
    ReviewItem,
    ReviewReport,
    RiskLevel,
    Verdict,
)
from smart_compliance.llm.registry import ProviderRegistry
from smart_compliance.parsing.pdf_parser import PdfParser
from smart_compliance.reporting import export_docx, export_json, export_pdf
from smart_compliance.ui.session import (
    clear_current_session,
    read_session_text,
    role_mismatch_warnings,
    session_directory,
)

st.set_page_config(page_title="智审合规助手", page_icon="🔎", layout="wide")


@dataclass(frozen=True, slots=True)
class SessionInput:
    """一次 Web 质检会话输入。"""

    procurement_bytes: bytes
    contract_bytes: bytes
    procurement_name: str
    contract_name: str
    model: str


def main() -> None:
    """渲染产品原型并执行当前会话质检。"""
    settings = Settings()
    endpoints = {endpoint.name: endpoint for endpoint in settings.endpoints}
    st.title("智审合规助手")
    st.error("教学模拟/测试数据 · 不得用于生产审批")
    st.caption("采购结果 ↔ 采购合同智能质检原型；AI 输出仅供辅助复核，不构成法律或审计结论。")
    model = st.selectbox(
        "质检引擎",
        ["rules", *sorted(endpoints)],
        format_func=lambda name: _model_label(name, endpoints),
        help="离线规则不会外发数据；外部模型会把解析后的授权测试文本发送至对应端点。",
    )
    external_confirmed = _external_confirmation(model, endpoints)
    left, right = st.columns(2)
    with left:
        procurement_upload = st.file_uploader("采购结果 PDF（源头依据）", type=["pdf"], key="procurement")
    with right:
        contract_upload = st.file_uploader("采购合同 PDF（质检对象）", type=["pdf"], key="contract")
    if procurement_upload is not None and contract_upload is not None:
        for warning in role_mismatch_warnings(procurement_upload.name, contract_upload.name):
            st.warning(warning)
    disabled = procurement_upload is None or contract_upload is None or not external_confirmed
    if st.button("开始质检", type="primary", disabled=disabled, use_container_width=True):
        if procurement_upload is not None and contract_upload is not None:
            _run_session(
                settings,
                SessionInput(
                    procurement_bytes=procurement_upload.getvalue(),
                    contract_bytes=contract_upload.getvalue(),
                    procurement_name=procurement_upload.name,
                    contract_name=contract_upload.name,
                    model=model,
                ),
            )
    _render_current_state(settings)


def _external_confirmation(model: str, endpoints: dict[str, ModelEndpoint]) -> bool:
    if model == "rules":
        st.info("当前为离线规则基线：文件内容不会发送到外部模型。")
        return True
    endpoint = endpoints[model]
    st.warning(f"外部调用：{endpoint.provider} / {endpoint.model}；解析文本将发送至 {endpoint.base_url}。")
    return st.checkbox(
        "我确认仅上传脱敏/授权测试数据，并同意发送至所选模型端点。",
        value=False,
        key=f"external-consent-{model}",
    )


def _run_session(settings: Settings, request: SessionInput) -> None:
    session_id = uuid4().hex
    session_dir = session_directory(settings, session_id)
    session_dir.mkdir(parents=True, exist_ok=False)
    procurement_path = session_dir / "procurement_result.pdf"
    contract_path = session_dir / "contract.pdf"
    procurement_path.write_bytes(request.procurement_bytes)
    contract_path.write_bytes(request.contract_bytes)
    _reset_session_state(session_id, session_dir, request)
    progress = st.progress(0, text="校验并解析采购结果 PDF")
    parser = PdfParser.from_settings(settings)
    try:
        procurement = parser.parse(procurement_path, DocumentRole.PROCUREMENT_RESULT)
        st.session_state["procurement_parsed"] = procurement
        progress.progress(35, text="校验并解析采购合同 PDF")
        contract = parser.parse(contract_path, DocumentRole.CONTRACT)
        st.session_state["contract_parsed"] = contract
        progress.progress(65, text="执行条款比对与结构校验")
        report = _compare(settings, request.model, procurement, contract, session_dir)
        progress.progress(85, text="生成 DOCX / PDF / JSON 报告")
        _export_outputs(report, session_dir)
    except (SmartComplianceError, ValueError, OSError) as error:
        progress.empty()
        st.session_state["review_error"] = str(error)
        return
    progress.progress(100, text="质检完成，等待人工复核")
    st.session_state["review_report"] = report


def _reset_session_state(session_id: str, session_dir: Path, request: SessionInput) -> None:
    for key in ("review_report", "review_error", "procurement_parsed", "contract_parsed"):
        st.session_state.pop(key, None)
    st.session_state["review_session_id"] = session_id
    st.session_state["review_output_dir"] = str(session_dir)
    st.session_state["review_model"] = request.model
    st.session_state["upload_names"] = (request.procurement_name, request.contract_name)


def _compare(
    settings: Settings,
    model: str,
    procurement: ParsedDocument,
    contract: ParsedDocument,
    session_dir: Path,
) -> ReviewReport:
    if model == "rules":
        return RuleBasedComparator(settings).compare(procurement, contract)
    provider = ProviderRegistry(settings).get(model)
    comparator = LlmComparator(provider, settings=settings)
    return asyncio.run(comparator.compare(procurement, contract, session_dir / "raw_model.txt"))


def _export_outputs(report: ReviewReport, session_dir: Path) -> None:
    export_json(report, session_dir / "review.json")
    export_docx(report, session_dir / "review.docx")
    export_pdf(report, session_dir / "review.pdf")


def _render_current_state(settings: Settings) -> None:
    output_value = st.session_state.get("review_output_dir")
    session_value = st.session_state.get("review_session_id")
    if not isinstance(output_value, str) or not isinstance(session_value, str):
        return
    output_dir = Path(output_value)
    st.caption(f"当前会话 ID：{session_value}　|　目录：{output_dir}")
    error = st.session_state.get("review_error")
    if isinstance(error, str):
        st.error(f"质检未完成：{error}")
        st.info("已完成的解析结果仍保留。可重试、切换离线规则引擎，或重新上传文件。")
        _render_parsing_details(output_dir)
    report = st.session_state.get("review_report")
    if isinstance(report, ReviewReport):
        _render_report(report, output_dir)
    _render_clear_controls(settings, session_value, output_dir)


def _render_report(report: ReviewReport, output_dir: Path) -> None:
    st.subheader(report.project_name)
    columns = st.columns(4)
    columns[0].metric("总体风险", report.summary.overall_risk.value.upper())
    second_label = "覆盖编码" if report.summary.covered_clause_count is not None else "一致项"
    second_value = (
        report.summary.covered_clause_count
        if report.summary.covered_clause_count is not None
        else report.summary.consistent_count
    )
    columns[1].metric(second_label, second_value)
    columns[2].metric("缺失项", report.summary.missing_count)
    columns[3].metric("差异项", report.summary.different_count)
    st.caption(
        f"运行 ID：{report.model.run_id}　|　{report.model.provider} / {report.model.model_name}　|　"
        f"证据校验：{report.evidence_validation_status.value}"
    )
    _render_filters_and_items(report)
    _render_parsing_details(output_dir)
    _render_model_audit(report, output_dir)
    _download_buttons(output_dir)


def _render_filters_and_items(report: ReviewReport) -> None:
    st.markdown("### 逐条质检结果")
    filter_columns = st.columns(5)
    verdicts = filter_columns[0].multiselect("结论", list(Verdict), default=list(Verdict), format_func=_verdict_label)
    risks = filter_columns[1].multiselect("风险", list(RiskLevel), default=list(RiskLevel), format_func=_risk_label)
    dimensions = filter_columns[2].multiselect(
        "维度", list(Dimension), default=list(Dimension), format_func=_dimension_label
    )
    minimum_confidence = filter_columns[3].slider("最低置信度", 0, 100, 0, format="%d%%") / 100
    review_filter = filter_columns[4].selectbox("人工复核", ["全部", "需要", "非必须"])
    selected = [
        item
        for item in report.items
        if item.verdict in verdicts
        and item.risk_level in risks
        and item.dimension in dimensions
        and item.confidence >= minimum_confidence
        and _matches_review_filter(item, review_filter)
    ]
    if not selected:
        st.info("当前筛选条件下没有条目。")
    for item in selected:
        _render_item(item)


def _render_item(item: ReviewItem) -> None:
    icon = "✅" if item.verdict == Verdict.CONSISTENT else "⚠️"
    title = f"{icon} {item.source_order}. {item.clause_name} · {_verdict_label(item.verdict)}"
    with st.expander(title, expanded=True):
        evidence_left, evidence_right = st.columns(2)
        evidence_left.markdown("**采购结果证据**")
        evidence_left.write(item.procurement_evidence.quote)
        evidence_left.caption(_locator(item.procurement_evidence.page, item.procurement_evidence.chunk_id))
        evidence_right.markdown("**合同证据**")
        evidence_right.write(item.contract_evidence.quote or "未找到对应证据")
        evidence_right.caption(_locator(item.contract_evidence.page, item.contract_evidence.chunk_id))
        st.write(f"**判断：** {item.difference}")
        st.write(f"**风险说明：** {item.risk_reason}")
        st.write(f"**复核建议：** {item.recommendation}")
        st.caption(f"置信度 {item.confidence:.0%} · {'需要人工复核' if item.needs_human_review else '建议抽样复核'}")


def _render_parsing_details(output_dir: Path) -> None:
    with st.expander("解析质量、清洗片段与警告", expanded=False):
        documents = (
            ("采购结果", st.session_state.get("procurement_parsed")),
            ("采购合同", st.session_state.get("contract_parsed")),
        )
        for label, value in documents:
            if not isinstance(value, ParsedDocument):
                st.write(f"{label}：尚未完成解析。")
                continue
            st.markdown(f"**{label}** · SHA-256 `{value.sha256}`")
            for warning in value.quality.warnings:
                st.warning(warning)
            st.dataframe(
                [
                    {
                        "chunk_id": chunk.chunk_id,
                        "页码": f"{chunk.page_start}-{chunk.page_end}",
                        "清洗文本": chunk.text,
                    }
                    for chunk in value.chunks
                ],
                use_container_width=True,
                hide_index=True,
            )
        st.caption(f"审计文件仅保存在当前会话目录：{output_dir}")


def _render_model_audit(report: ReviewReport, output_dir: Path) -> None:
    with st.expander("模型运行与证据校验审计", expanded=False):
        st.json(
            {
                "run_id": report.model.run_id,
                "provider": report.model.provider,
                "model": report.model.model_name,
                "prompt_version": report.model.prompt_version,
                "input_hashes": report.source_hashes,
                "evidence_validation": report.evidence_validation_status.value,
                "validation_warnings": report.validation_warnings,
                "needs_human_review": report.summary.needs_human_review,
            }
        )
        if report.model.raw_output_file:
            raw_path = Path(report.model.raw_output_file)
            st.caption(f"原始输出引用：{raw_path}")
            st.code(read_session_text(output_dir, raw_path), language="json")
            validation_text = read_session_text(output_dir, raw_path.with_suffix(".validation.json"))
            try:
                st.json(json.loads(validation_text))
            except json.JSONDecodeError:
                st.code(validation_text)
        else:
            st.info("当前为离线规则结果，不存在 LLM 原始输出。")


def _render_clear_controls(settings: Settings, session_id: str, output_dir: Path) -> None:
    st.markdown("### 当前会话数据")
    confirmed = st.checkbox(
        f"确认永久删除当前会话目录：{output_dir}",
        value=False,
        key=f"clear-confirm-{session_id}",
    )
    if st.button("清空当前会话数据", disabled=not confirmed, key=f"clear-button-{session_id}"):
        try:
            clear_current_session(settings, session_id, output_dir)
        except (ValueError, OSError) as error:
            st.error(f"清理失败：{error}")
            return
        for key in list(st.session_state):
            is_review_key = isinstance(key, str) and key.startswith("review_")
            if is_review_key or key in {"procurement_parsed", "contract_parsed", "upload_names"}:
                del st.session_state[key]
        st.success("当前会话数据已永久删除。")


def _download_buttons(output_dir: Path) -> None:
    st.markdown("### 导出报告")
    columns = st.columns(3)
    files = (
        (
            columns[0],
            "review.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "下载 DOCX",
        ),
        (columns[1], "review.pdf", "application/pdf", "下载 PDF"),
        (columns[2], "review.json", "application/json", "下载 JSON"),
    )
    for column, filename, mime, label in files:
        path = output_dir / filename
        column.download_button(label, data=path.read_bytes(), file_name=filename, mime=mime, use_container_width=True)


def _model_label(name: str, endpoints: dict[str, ModelEndpoint]) -> str:
    if name == "rules":
        return "离线规则基线（不外发数据）"
    endpoint = endpoints[name]
    return f"{endpoint.provider} / {endpoint.model}（数据出域）"


def _matches_review_filter(item: ReviewItem, selected: str) -> bool:
    return (
        selected == "全部"
        or (selected == "需要" and item.needs_human_review)
        or (selected == "非必须" and not item.needs_human_review)
    )


def _locator(page: int | None, chunk_id: str | None) -> str:
    return f"第 {page} 页 · {chunk_id}" if page is not None else "无对应页码"


def _verdict_label(verdict: Verdict) -> str:
    return {Verdict.CONSISTENT: "一致项", Verdict.MISSING: "缺失项", Verdict.DIFFERENT: "差异项"}[verdict]


def _risk_label(risk: RiskLevel) -> str:
    return {RiskLevel.LOW: "低", RiskLevel.MEDIUM: "中", RiskLevel.HIGH: "高"}[risk]


def _dimension_label(dimension: Dimension) -> str:
    return {
        Dimension.BUSINESS: "商务",
        Dimension.TIME: "时间",
        Dimension.LEGAL: "法律",
        Dimension.OTHER: "其他",
    }[dimension]


if __name__ == "__main__":
    main()
