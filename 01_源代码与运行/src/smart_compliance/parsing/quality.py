"""解析质量指标。"""

import re

from smart_compliance.domain.models import ParsingQuality, RemovedText

NORMAL_CHARACTER = re.compile(r"[\u4e00-\u9fffA-Za-z0-9\s，。！？；：、（）()《》【】\[\]“”'\".,:;!?%￥¥+\-*/=<>]")


def abnormal_character_ratio(text: str) -> float:
    """计算异常字符比例。

    Args:
        text: 待检查文本。

    Returns:
        0 到 1 之间的异常字符比例。
    """
    if not text:
        return 0.0
    normal = sum(bool(NORMAL_CHARACTER.fullmatch(character)) for character in text)
    return round(1 - normal / len(text), 6)


def build_quality(
    raw_page_texts: list[str],
    cleaned_page_texts: list[str],
    decisions: list[RemovedText],
) -> ParsingQuality:
    """构建解析质量摘要。

    Args:
        raw_page_texts: 每页原始文本。
        cleaned_page_texts: 每页清洗文本。
        decisions: 水印和页眉页脚处理记录。

    Returns:
        解析质量模型。
    """
    blank_pages = [index for index, text in enumerate(cleaned_page_texts, start=1) if len(text.strip()) < 5]
    low_quality_pages = [
        index
        for index, text in enumerate(cleaned_page_texts, start=1)
        if len(text.strip()) < 30 or abnormal_character_ratio(text) > 0.12
    ]
    removed_watermarks = sum(item.reason == "文本层水印候选" and item.removed for item in decisions)
    retained_watermarks = sum(item.reason == "文本层水印候选" and not item.removed for item in decisions)
    removed_headers = sum(item.reason == "重复页眉/页脚" and item.removed for item in decisions)
    cleaned_text = "\n".join(cleaned_page_texts)
    warnings: list[str] = []
    if blank_pages:
        warnings.append("存在几乎无文本的页面，可能是扫描页或解析失败。")
    if low_quality_pages:
        warnings.append("部分页面文本质量偏低，建议人工复核或启用 OCR。")
    if retained_watermarks:
        warnings.append("存在低置信水印候选，已保留以避免误删正文。")
    return ParsingQuality(
        page_count=len(raw_page_texts),
        raw_characters=sum(len(text) for text in raw_page_texts),
        cleaned_characters=len(cleaned_text),
        removed_watermark_candidates=removed_watermarks,
        retained_watermark_candidates=retained_watermarks,
        removed_header_footer_lines=removed_headers,
        abnormal_character_ratio=abnormal_character_ratio(cleaned_text),
        blank_pages=blank_pages,
        low_quality_pages=low_quality_pages,
        needs_human_review=bool(low_quality_pages or retained_watermarks),
        warnings=warnings,
    )
