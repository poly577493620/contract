"""逐页图片渲染、本地 OCR、质量门禁和可复现缓存。"""

import hashlib
import json
import os
import shutil
import statistics
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pymupdf as fitz

from smart_compliance.domain.exceptions import OcrQualityError
from smart_compliance.domain.models import (
    DocumentRole,
    OcrMetadata,
    OcrPageQuality,
    OcrTextSpan,
    ParsedDocument,
    ParsingQuality,
)
from smart_compliance.parsing.cleaner import build_chunks, normalize_line
from smart_compliance.parsing.ocr_engine import OcrEngine, RapidOcrEngine
from smart_compliance.parsing.quality import abnormal_character_ratio
from smart_compliance.parsing.reading_order import OrderedPage, order_page_fragments


@dataclass(frozen=True, slots=True)
class OcrOptions:
    """影响 OCR 结果、缓存键和质量门禁的配置。"""

    default_dpi: int = 300
    retry_dpi: int = 360
    min_text_score: float = 0.50
    low_confidence_score: float = 0.85
    min_median_confidence: float = 0.85
    max_low_confidence_ratio: float = 0.15
    max_fragment_line_ratio: float = 0.35


@dataclass(frozen=True, slots=True)
class _OcrAttempt:
    source: Path
    role: DocumentRole
    pdf_sha256: str
    dpi: int
    attempted_dpis: list[int]
    cache_dir: Path


class PageImageOcrParser:
    """只使用页面图片 OCR 生成模型输入，不回退到 PDF 文本层。"""

    def __init__(
        self,
        cache_root: Path,
        options: OcrOptions,
        engine: OcrEngine | None = None,
    ) -> None:
        """初始化 OCR 管线。"""
        self._cache_root = cache_root.resolve()
        self._options = options
        self._engine = engine or RapidOcrEngine()

    def parse(self, source: Path, role: DocumentRole, pdf_sha256: str) -> ParsedDocument:
        """依次尝试 300/360 DPI，并只返回通过门禁的页面 OCR。"""
        attempted: list[int] = []
        for dpi in dict.fromkeys((self._options.default_dpi, self._options.retry_dpi)):
            attempted.append(dpi)
            cache_dir = self._cache_directory(pdf_sha256, dpi)
            cached = self._load_cache(cache_dir, source, role, pdf_sha256)
            if cached is not None:
                cached = _with_attempted_dpis(cached, attempted)
                if cached.ocr_metadata and cached.ocr_metadata.quality_gate_passed:
                    return cached
                continue
            attempt = _OcrAttempt(source, role, pdf_sha256, dpi, list(attempted), cache_dir)
            parsed = self._recognize_document(attempt)
            self._write_cache(cache_dir, parsed)
            if parsed.ocr_metadata and parsed.ocr_metadata.quality_gate_passed:
                return parsed
        raise OcrQualityError(f"ocr_quality_failed: {source.name} 在 DPI {attempted} 下均未通过页面图片 OCR 质量门禁。")

    def _recognize_document(
        self,
        attempt: _OcrAttempt,
    ) -> ParsedDocument:
        pages_dir = attempt.cache_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        ordered_pages: list[OrderedPage] = []
        page_hashes: dict[str, str] = {}
        page_sizes: list[tuple[int, int]] = []
        with fitz.open(attempt.source) as document:
            for page_index in range(document.page_count):
                page_number = page_index + 1
                pixmap = document.load_page(page_index).get_pixmap(dpi=attempt.dpi, colorspace=fitz.csRGB, alpha=False)
                canonical_path = pages_dir / f"page-{page_number:04d}.png"
                image_path = _page_image(pixmap, canonical_path)
                image_sha256 = _sha256(image_path)
                page_hashes[str(page_number)] = image_sha256
                page_sizes.append((pixmap.width, pixmap.height))
                fragments = self._engine.recognize(image_path, self._options.min_text_score)
                ordered_pages.append(
                    order_page_fragments(
                        fragments,
                        page=page_number,
                        page_width=float(pixmap.width),
                        image_sha256=image_sha256,
                    )
                )
        page_texts = [_clean_ocr_lines(page.lines) for page in ordered_pages]
        all_spans = [span for page in ordered_pages for span in page.spans]
        page_quality = [self._page_quality(page, page_sizes[index]) for index, page in enumerate(ordered_pages)]
        quality = self._document_quality(page_texts, all_spans, page_quality)
        prefix = {
            DocumentRole.PROCUREMENT_RESULT: "result",
            DocumentRole.CONTRACT: "contract",
            DocumentRole.REFERENCE_ANSWER: "reference",
        }[attempt.role]
        cleaned_text = "\n\n".join(page_texts)
        text_sha256 = hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()
        cache_key = attempt.cache_dir.name
        metadata = OcrMetadata(
            pdf_sha256=attempt.pdf_sha256,
            text_sha256=text_sha256,
            cache_key=cache_key,
            cache_dir=str(attempt.cache_dir),
            renderer="PyMuPDF",
            renderer_version=fitz.VersionBind,
            dpi=attempt.dpi,
            attempted_dpis=attempt.attempted_dpis,
            ocr_engine=self._engine.name,
            ocr_engine_version=self._engine.version,
            ocr_model_fingerprint=self._engine.model_fingerprint,
            page_image_sha256=page_hashes,
            page_quality=page_quality,
            quality_gate_passed=bool(quality.ocr_quality_gate_passed),
        )
        return ParsedDocument(
            role=attempt.role,
            source_name=attempt.source.name,
            sha256=attempt.pdf_sha256,
            raw_text=cleaned_text,
            cleaned_text=cleaned_text,
            chunks=build_chunks(page_texts, prefix),
            quality=quality,
            ocr_source="page_image",
            ocr_text_sha256=text_sha256,
            ocr_spans=all_spans,
            ocr_metadata=metadata,
        )

    def _page_quality(self, page: OrderedPage, page_size: tuple[int, int]) -> OcrPageQuality:
        scores = [span.confidence for span in page.spans]
        low_ratio = _ratio(sum(score < self._options.low_confidence_score for score in scores), len(scores))
        fragment_ratio = _fragment_ratio(page)
        coverage = _box_coverage(page.spans, page_size)
        characters = sum(len(line.replace("\t", "")) for line in page.lines)
        median_confidence = statistics.median(scores) if scores else 0.0
        passed = bool(scores) and median_confidence >= self._options.min_median_confidence
        passed = passed and low_ratio <= self._options.max_low_confidence_ratio
        passed = passed and fragment_ratio <= self._options.max_fragment_line_ratio
        return OcrPageQuality(
            page=page.page,
            character_count=characters,
            span_count=len(scores),
            mean_confidence=statistics.fmean(scores) if scores else 0.0,
            median_confidence=median_confidence,
            low_confidence_ratio=low_ratio,
            fragment_line_ratio=fragment_ratio,
            text_box_coverage=coverage,
            blank=characters < 5,
            column_mode=page.column_mode,
            quality_gate_passed=passed,
        )

    def _document_quality(
        self,
        page_texts: list[str],
        spans: list[OcrTextSpan],
        page_quality: list[OcrPageQuality],
    ) -> ParsingQuality:
        scores = [span.confidence for span in spans]
        low_ratio = _ratio(sum(score < self._options.low_confidence_score for score in scores), len(scores))
        fragment_ratio = _ratio(
            sum(metric.fragment_line_ratio * max(metric.span_count, 1) for metric in page_quality),
            sum(max(metric.span_count, 1) for metric in page_quality),
        )
        median_confidence = statistics.median(scores) if scores else 0.0
        passed = bool(scores) and median_confidence >= self._options.min_median_confidence
        passed = passed and low_ratio <= self._options.max_low_confidence_ratio
        passed = passed and fragment_ratio <= self._options.max_fragment_line_ratio
        passed = passed and all(metric.quality_gate_passed for metric in page_quality)
        blank_pages = [metric.page for metric in page_quality if metric.blank]
        low_quality_pages = [metric.page for metric in page_quality if not metric.quality_gate_passed]
        warnings: list[str] = []
        if blank_pages:
            warnings.append("页面图片 OCR 检测到几乎无文字的页面。")
        if low_quality_pages:
            warnings.append("部分页面 OCR 未达到页级建议阈值，已按文档级指标决定是否重试。")
        if not passed:
            warnings.append("页面图片 OCR 未通过文档质量门禁，禁止回退到 PDF 文本层。")
        cleaned_text = "\n".join(page_texts)
        return ParsingQuality(
            page_count=len(page_texts),
            raw_characters=len(cleaned_text),
            cleaned_characters=len(cleaned_text),
            removed_watermark_candidates=0,
            retained_watermark_candidates=0,
            removed_header_footer_lines=0,
            abnormal_character_ratio=abnormal_character_ratio(cleaned_text),
            blank_pages=blank_pages,
            low_quality_pages=low_quality_pages,
            needs_human_review=bool(low_quality_pages),
            warnings=warnings,
            ocr_source="page_image",
            ocr_mean_confidence=statistics.fmean(scores) if scores else 0.0,
            ocr_median_confidence=median_confidence,
            ocr_low_confidence_ratio=low_ratio,
            ocr_fragment_line_ratio=fragment_ratio,
            ocr_quality_gate_passed=passed,
        )

    def _cache_directory(self, pdf_sha256: str, dpi: int) -> Path:
        payload = {
            "dpi": dpi,
            "engine": self._engine.name,
            "engine_version": self._engine.version,
            "model_fingerprint": self._engine.model_fingerprint,
            "renderer": "PyMuPDF",
            "renderer_version": fitz.VersionBind,
            "min_text_score": self._options.min_text_score,
            "low_confidence_score": self._options.low_confidence_score,
            "min_median_confidence": self._options.min_median_confidence,
            "max_low_confidence_ratio": self._options.max_low_confidence_ratio,
            "max_fragment_line_ratio": self._options.max_fragment_line_ratio,
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return self._cache_root / pdf_sha256 / f"rapidocr-{self._engine.version}-pymupdf-{dpi}-{fingerprint}"

    @staticmethod
    def _load_cache(
        cache_dir: Path,
        source: Path,
        role: DocumentRole,
        pdf_sha256: str,
    ) -> ParsedDocument | None:
        manifests = [cache_dir / "document.json", *sorted(cache_dir.glob("document-recovery-*.json"), reverse=True)]
        for manifest_path in manifests:
            if not manifest_path.exists():
                continue
            try:
                parsed = ParsedDocument.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if parsed.sha256 != pdf_sha256 or parsed.ocr_source != "page_image":
                continue
            return _adapt_cached_document(parsed, source.name, role)
        return None

    @staticmethod
    def _write_cache(cache_dir: Path, parsed: ParsedDocument) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_text_once(
            cache_dir / "ocr-spans.json",
            json.dumps([span.model_dump(mode="json") for span in parsed.ocr_spans], ensure_ascii=False, indent=2),
        )
        _write_text_once(cache_dir / "cleaned.txt", parsed.cleaned_text)
        if parsed.ocr_metadata is not None:
            _write_text_once(cache_dir / "quality.json", parsed.ocr_metadata.model_dump_json(indent=2))
        manifest = cache_dir / "document.json"
        payload = parsed.model_dump_json(indent=2)
        _write_text_once(manifest, payload)
        if not _valid_manifest(manifest, parsed.sha256):
            recovery = cache_dir / f"document-recovery-{uuid4().hex}.json"
            _write_text_once(recovery, payload)


def _with_attempted_dpis(document: ParsedDocument, attempted_dpis: list[int]) -> ParsedDocument:
    if document.ocr_metadata is None:
        return document
    metadata = document.ocr_metadata.model_copy(update={"attempted_dpis": list(attempted_dpis)})
    return document.model_copy(update={"ocr_metadata": metadata})


def _adapt_cached_document(parsed: ParsedDocument, source_name: str, role: DocumentRole) -> ParsedDocument:
    """让同一 PDF 内容的缓存适配本次上传名和文档角色。"""
    prefix = {
        DocumentRole.PROCUREMENT_RESULT: "result",
        DocumentRole.CONTRACT: "contract",
        DocumentRole.REFERENCE_ANSWER: "reference",
    }[role]
    page_counts: dict[int, int] = {}
    chunks = []
    for chunk in parsed.chunks:
        page_counts[chunk.page_start] = page_counts.get(chunk.page_start, 0) + 1
        chunk_id = f"{prefix}-p{chunk.page_start}-c{page_counts[chunk.page_start]}"
        chunks.append(chunk.model_copy(update={"chunk_id": chunk_id}))
    return parsed.model_copy(update={"role": role, "source_name": source_name, "chunks": chunks})


def _page_image(pixmap: fitz.Pixmap, canonical_path: Path) -> Path:
    """复用有效页面图；缺失时以唯一临时文件渲染并只发布一次。"""
    if _valid_page_image(canonical_path, pixmap.width, pixmap.height):
        return canonical_path
    temporary = canonical_path.with_name(f".{canonical_path.stem}-{uuid4().hex}.png")
    pixmap.save(temporary)
    digest = _sha256(temporary)
    fallback = canonical_path.with_name(f"{canonical_path.stem}-{digest[:12]}.png")
    for target in (canonical_path, fallback):
        _publish_file_once(temporary, target)
        if _same_digest(target, digest):
            _try_unlink(temporary)
            return target
    return temporary


def _valid_page_image(path: Path, width: int, height: int) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        image = fitz.Pixmap(path)
    except (OSError, RuntimeError, ValueError, fitz.mupdf.FzErrorBase):
        return False
    return bool(image.width == width and image.height == height)


def _publish_file_once(source: Path, target: Path) -> None:
    """不覆盖已有缓存文件，避免 Windows 下删除被占用文件。"""
    try:
        os.link(source, target)
        return
    except FileExistsError:
        return
    except OSError:
        pass
    try:
        with source.open("rb") as input_stream, target.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
    except FileExistsError:
        return


def _write_text_once(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    _publish_file_once(temporary, path)
    _try_unlink(temporary)


def _valid_manifest(path: Path, pdf_sha256: str) -> bool:
    try:
        parsed = ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return parsed.sha256 == pdf_sha256 and parsed.ocr_source == "page_image"


def _same_digest(path: Path, expected: str) -> bool:
    try:
        return path.is_file() and _sha256(path) == expected
    except OSError:
        return False


def _try_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _clean_ocr_lines(lines: list[str]) -> str:
    return "\n".join(line for line in (normalize_line(value) for value in lines) if line)


def _fragment_ratio(page: OrderedPage) -> float:
    if not page.lines:
        return 1.0
    fragments = sum(len(line.replace("\t", "").strip()) <= 1 for line in page.lines)
    return _ratio(fragments, len(page.lines))


def _box_coverage(spans: list[OcrTextSpan], page_size: tuple[int, int]) -> float:
    width, height = page_size
    page_area = max(1.0, float(width * height))
    box_area = sum(max(0.0, span.bbox[2] - span.bbox[0]) * max(0.0, span.bbox[3] - span.bbox[1]) for span in spans)
    return min(1.0, round(box_area / page_area, 6))


def _ratio(numerator: float, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
