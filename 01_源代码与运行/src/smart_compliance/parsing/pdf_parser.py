"""逐页图片 OCR 的可审计 PDF 解析入口。"""

import hashlib
from pathlib import Path

import pymupdf as fitz

from smart_compliance.config import Settings, project_root
from smart_compliance.domain.exceptions import PdfParsingError, PdfValidationError, SmartComplianceError
from smart_compliance.domain.models import DocumentRole, ParsedDocument
from smart_compliance.parsing.ocr_engine import OcrEngine
from smart_compliance.parsing.ocr_parser import OcrOptions, PageImageOcrParser


class PdfParser:
    """校验 PDF 后，强制使用页面图片和本地 OCR 构建模型输入。"""

    def __init__(
        self,
        max_size_mb: int = 25,
        max_pages: int = 200,
        *,
        cache_dir: Path | None = None,
        ocr_options: OcrOptions | None = None,
        ocr_engine: OcrEngine | None = None,
    ) -> None:
        """初始化解析器。

        Args:
            max_size_mb: 最大文件大小（MiB）。
            max_pages: 最大页数。
            cache_dir: 项目内 OCR 缓存根目录。
            ocr_options: 渲染、识别和质量门禁配置。
            ocr_engine: 可替换的本地 OCR 引擎。
        """
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._max_pages = max_pages
        selected_cache = cache_dir or project_root() / "outputs" / "ocr-cache"
        self._ocr = PageImageOcrParser(selected_cache, ocr_options or OcrOptions(), ocr_engine)

    @classmethod
    def from_settings(cls, settings: Settings, *, ocr_engine: OcrEngine | None = None) -> "PdfParser":
        """使用经过边界校验的应用配置创建解析器。"""
        return cls(
            settings.max_upload_mb,
            settings.max_pdf_pages,
            cache_dir=settings.writable_path(settings.ocr_cache_dir),
            ocr_options=OcrOptions(
                default_dpi=settings.ocr_default_dpi,
                retry_dpi=settings.ocr_retry_dpi,
                min_text_score=settings.ocr_min_text_score,
                low_confidence_score=settings.ocr_low_confidence_score,
                min_median_confidence=settings.ocr_min_median_confidence,
                max_low_confidence_ratio=settings.ocr_max_low_confidence_ratio,
                max_fragment_line_ratio=settings.ocr_max_fragment_line_ratio,
            ),
            ocr_engine=ocr_engine,
        )

    def parse(self, path: Path, role: DocumentRole) -> ParsedDocument:
        """从逐页 PNG 的本地 OCR 结果构建可追溯文档。

        Args:
            path: PDF 文件路径。
            role: 文档角色。

        Returns:
            仅来源于页面图片 OCR 的解析结果。

        Raises:
            PdfValidationError: 文件签名、大小、加密状态或页数不符合要求。
            PdfParsingError: PDF 渲染或 OCR 失败。
        """
        source = path.resolve()
        self._validate_file(source)
        try:
            with fitz.open(source) as document:
                self._validate_document(document)
        except PdfValidationError:
            raise
        except (fitz.FileDataError, RuntimeError, ValueError) as error:
            raise PdfParsingError(f"PDF 页面验证失败: {source.name}: {error}") from error
        try:
            return self._ocr.parse(source, role, self._sha256(source))
        except SmartComplianceError:
            raise
        except (OSError, RuntimeError, ValueError, fitz.mupdf.FzErrorBase) as error:
            raise PdfParsingError(f"PDF 页面 OCR 失败: {source.name}: {type(error).__name__}") from error

    def _validate_file(self, path: Path) -> None:
        if path.suffix.lower() != ".pdf":
            raise PdfValidationError("仅支持 .pdf 文件。")
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                signature = stream.read(5)
        except FileNotFoundError as error:
            raise PdfValidationError(f"文件不存在: {path}") from error
        except PermissionError as error:
            raise PdfValidationError(f"无权读取文件: {path}") from error
        if size == 0:
            raise PdfValidationError("PDF 文件为空。")
        if size > self._max_size_bytes:
            raise PdfValidationError(f"PDF 超过大小限制: {size} bytes。")
        if signature != b"%PDF-":
            raise PdfValidationError("文件扩展名为 PDF，但文件签名无效。")

    def _validate_document(self, document: fitz.Document) -> None:
        if document.needs_pass:
            raise PdfValidationError("PDF 已加密，请先提供解密后的授权测试文件。")
        if document.page_count > self._max_pages:
            raise PdfValidationError(f"PDF 页数 {document.page_count} 超过上限 {self._max_pages}。")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()
