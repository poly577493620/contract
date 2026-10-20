"""页面图片 OCR、缓存、DPI 重试和 PDF 校验。"""

from pathlib import Path

import pymupdf as fitz
import pytest

from smart_compliance.domain.exceptions import OcrQualityError, PdfValidationError
from smart_compliance.domain.models import DocumentRole
from smart_compliance.parsing.ocr_engine import RawOcrFragment
from smart_compliance.parsing.ocr_parser import OcrOptions
from smart_compliance.parsing.pdf_parser import PdfParser


class FakeOcrEngine:
    """按调用次数返回预置 OCR 片段。"""

    name = "fake-ocr"
    version = "1.0"
    model_fingerprint = "f" * 64

    def __init__(self, pages: list[list[RawOcrFragment]]) -> None:
        self._pages = pages
        self.calls = 0

    def recognize(self, image_path: Path, text_score: float) -> list[RawOcrFragment]:
        """返回下一页结果，并确认输入确为 PNG 页面。"""
        assert image_path.suffix == ".png"
        assert text_score == 0.5
        selected = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return selected


def _fragments(text: str, confidence: float = 0.96) -> list[RawOcrFragment]:
    return [RawOcrFragment(text, (50.0, 80.0, 500.0, 120.0), confidence)]


def _build_pdf(path: Path, pages: int = 1, text_layer: str = "TEXT LAYER MUST NOT BE USED") -> None:
    document = fitz.open()
    for page_number in range(pages):
        page = document.new_page()
        page.insert_text((72, 100), f"{text_layer} {page_number + 1}")
    document.save(path)
    document.close()


def _parser(tmp_path: Path, engine: FakeOcrEngine) -> PdfParser:
    return PdfParser(cache_dir=tmp_path / "cache", ocr_engine=engine)


def test_parser_uses_page_image_ocr_and_keeps_traceability(tmp_path: Path) -> None:
    """正式文本必须来自页面 PNG OCR，并记录哈希、坐标和引擎元数据。"""
    path = tmp_path / "sample.pdf"
    _build_pdf(path)
    engine = FakeOcrEngine([_fragments("验收合格后15日内付款")])
    parsed = _parser(tmp_path, engine).parse(path, DocumentRole.PROCUREMENT_RESULT)
    assert parsed.cleaned_text == "验收合格后15日内付款"
    assert "TEXT LAYER" not in parsed.cleaned_text
    assert parsed.ocr_source == "page_image"
    assert parsed.ocr_text_sha256 is not None
    assert parsed.ocr_metadata is not None
    assert parsed.ocr_metadata.quality_gate_passed is True
    assert parsed.ocr_metadata.page_image_sha256["1"] == parsed.ocr_spans[0].page_image_sha256
    assert parsed.ocr_spans[0].line_id == "p1-l1"


def test_ocr_cache_avoids_repeated_inference(tmp_path: Path) -> None:
    """同一 PDF 和配置再次解析时应读取完整缓存。"""
    path = tmp_path / "cached.pdf"
    _build_pdf(path)
    engine = FakeOcrEngine([_fragments("采购合同金额人民币100元")])
    parser = _parser(tmp_path, engine)
    first = parser.parse(path, DocumentRole.CONTRACT)
    second = parser.parse(path, DocumentRole.CONTRACT)
    assert engine.calls == 1
    assert first.ocr_text_sha256 == second.ocr_text_sha256
    assert first.ocr_metadata and Path(first.ocr_metadata.cache_dir, "pages", "page-0001.png").exists()


def test_ocr_cache_is_reused_when_uploaded_filename_changes(tmp_path: Path) -> None:
    """同一 PDF 被 Streamlit 改名保存后仍应复用内容哈希缓存。"""
    original = tmp_path / "JZC-2026-005_采购结果.pdf"
    uploaded = tmp_path / "procurement_result.pdf"
    _build_pdf(original)
    uploaded.write_bytes(original.read_bytes())
    engine = FakeOcrEngine([_fragments("验收合格后15日内付款")])
    parser = _parser(tmp_path, engine)
    parser.parse(original, DocumentRole.PROCUREMENT_RESULT)
    parsed = parser.parse(uploaded, DocumentRole.PROCUREMENT_RESULT)
    assert engine.calls == 1
    assert parsed.source_name == "procurement_result.pdf"
    assert parsed.chunks[0].chunk_id == "result-p1-c1"


def test_ocr_cache_adapts_chunk_prefix_for_document_role(tmp_path: Path) -> None:
    """内容缓存可跨角色复用，但证据 chunk ID 必须匹配本次角色。"""
    path = tmp_path / "same.pdf"
    _build_pdf(path)
    engine = FakeOcrEngine([_fragments("测试条款")])
    parser = _parser(tmp_path, engine)
    parser.parse(path, DocumentRole.PROCUREMENT_RESULT)
    parsed = parser.parse(path, DocumentRole.CONTRACT)
    assert engine.calls == 1
    assert parsed.role == DocumentRole.CONTRACT
    assert parsed.chunks[0].chunk_id == "contract-p1-c1"


def test_partial_cache_reuses_existing_page_image_without_overwrite(tmp_path: Path) -> None:
    """清单缺失时应复用有效 PNG，而不是覆盖可能被占用的页面图。"""
    path = tmp_path / "partial.pdf"
    _build_pdf(path)
    engine = FakeOcrEngine([_fragments("测试条款")])
    parser = _parser(tmp_path, engine)
    first = parser.parse(path, DocumentRole.CONTRACT)
    assert first.ocr_metadata is not None
    cache_dir = Path(first.ocr_metadata.cache_dir)
    page_path = cache_dir / "pages" / "page-0001.png"
    original_hash = first.ocr_metadata.page_image_sha256["1"]
    original_mtime = page_path.stat().st_mtime_ns
    (cache_dir / "document.json").unlink()

    second = parser.parse(path, DocumentRole.CONTRACT)

    assert engine.calls == 2
    assert second.ocr_metadata is not None
    assert second.ocr_metadata.page_image_sha256["1"] == original_hash
    assert page_path.stat().st_mtime_ns == original_mtime


def test_low_quality_300_dpi_retries_once_at_360(tmp_path: Path) -> None:
    """300 DPI 未达阈值时只允许一次 360 DPI 重识别。"""
    path = tmp_path / "retry.pdf"
    _build_pdf(path)
    engine = FakeOcrEngine([_fragments("低置信识别文字", 0.60), _fragments("高置信识别文字", 0.97)])
    parser = PdfParser(
        cache_dir=tmp_path / "cache",
        ocr_engine=engine,
        ocr_options=OcrOptions(default_dpi=300, retry_dpi=360),
    )
    parsed = parser.parse(path, DocumentRole.CONTRACT)
    assert engine.calls == 2
    assert parsed.cleaned_text == "高置信识别文字"
    assert parsed.ocr_metadata is not None
    assert parsed.ocr_metadata.dpi == 360
    assert parsed.ocr_metadata.attempted_dpis == [300, 360]


def test_ocr_failure_never_falls_back_to_text_layer(tmp_path: Path) -> None:
    """两次 OCR 均失败时，即使 PDF 有文本层也必须明确终止。"""
    path = tmp_path / "no-fallback.pdf"
    _build_pdf(path, text_layer="验收合格后15日内付款")
    engine = FakeOcrEngine([[], []])
    with pytest.raises(OcrQualityError, match="ocr_quality_failed"):
        _parser(tmp_path, engine).parse(path, DocumentRole.CONTRACT)
    assert engine.calls == 2


def test_invalid_pdf_signature(tmp_path: Path) -> None:
    """伪 PDF 应在 OCR 前失败。"""
    path = tmp_path / "invalid.pdf"
    path.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(PdfValidationError, match="文件签名无效"):
        _parser(tmp_path, FakeOcrEngine([[]])).parse(path, DocumentRole.CONTRACT)


def test_page_limit_is_validation_error(tmp_path: Path) -> None:
    """页数超限应保留为可操作的校验错误。"""
    path = tmp_path / "too-many-pages.pdf"
    _build_pdf(path, pages=2)
    parser = PdfParser(max_pages=1, cache_dir=tmp_path / "cache", ocr_engine=FakeOcrEngine([[]]))
    with pytest.raises(PdfValidationError, match="页数 2 超过上限 1"):
        parser.parse(path, DocumentRole.CONTRACT)
