"""005/006 授权教学数据的真实页面图片 OCR 回归。"""

import re
from pathlib import Path

import pytest

from smart_compliance.config import Settings
from smart_compliance.domain.models import DocumentRole
from smart_compliance.parsing.pdf_parser import PdfParser


def _sample_file(root: Path, sample_id: str, suffix: str) -> Path:
    matches = list((root / sample_id).glob(f"*_{suffix}.pdf"))
    if len(matches) != 1:
        raise ValueError(f"样本 {sample_id} 的 {suffix} PDF 数量不是 1")
    return matches[0]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


@pytest.mark.integration
def test_005_payment_and_contract_term_are_read_from_page_images() -> None:
    """005 的 15/30 日付款期限和采购合同期限应能从页面图像回查。"""
    settings = Settings()
    root = settings.root / "data" / "raw" / "智审合规助手数据集" / "数据集_无水印"
    parser = PdfParser.from_settings(settings)
    procurement = parser.parse(_sample_file(root, "005", "采购结果"), DocumentRole.PROCUREMENT_RESULT)
    contract = parser.parse(_sample_file(root, "005", "采购合同"), DocumentRole.CONTRACT)
    assert procurement.ocr_source == contract.ocr_source == "page_image"
    assert "验收合格后15日内" in _compact(procurement.cleaned_text)
    assert "验收合格后30日内" in _compact(contract.cleaned_text)
    assert "2026年12月31日" in _compact(procurement.cleaned_text)


@pytest.mark.integration
def test_006_value_added_services_are_read_from_page_images() -> None:
    """006 的培训和应急响应组合要求应能从页面图片 OCR 回查。"""
    settings = Settings()
    root = settings.root / "data" / "raw" / "智审合规助手数据集" / "数据集_无水印"
    parser = PdfParser.from_settings(settings)
    procurement = parser.parse(_sample_file(root, "006", "采购结果"), DocumentRole.PROCUREMENT_RESULT)
    contract = parser.parse(_sample_file(root, "006", "采购合同"), DocumentRole.CONTRACT)
    procurement_text = _compact(procurement.cleaned_text)
    assert procurement.ocr_source == contract.ocr_source == "page_image"
    assert "免费技术培训" in procurement_text
    assert "24小时应急响应" in procurement_text or "7×24小时应急响应" in procurement_text
    assert "小时内" in procurement_text
