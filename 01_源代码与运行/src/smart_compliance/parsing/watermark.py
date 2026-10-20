"""文本层水印与重复页眉页脚识别。"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from smart_compliance.domain.models import RemovedText, TextSpan
from smart_compliance.parsing.cleaner import normalize_line

WATERMARK_HINT = re.compile(r"(?:仅供|教学|模拟|水印|智审|内部资料|测试数据)")


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """页面尺寸。"""

    width: float
    height: float


@dataclass(frozen=True, slots=True)
class FilterResult:
    """水印和页眉页脚过滤结果。"""

    kept: list[TextSpan]
    removed: list[RemovedText]


def filter_noise(spans: list[TextSpan], geometries: dict[int, PageGeometry]) -> FilterResult:
    """识别重复页眉页脚和文本层水印。

    Args:
        spans: 全部文本片段。
        geometries: 页码到页面尺寸的映射。

    Returns:
        保留片段与候选审计记录。
    """
    normalized = {index: _key(span.text) for index, span in enumerate(spans)}
    page_occurrences: dict[str, set[int]] = defaultdict(set)
    total_occurrences: Counter[str] = Counter()
    for index, span in enumerate(spans):
        key = normalized[index]
        if key:
            page_occurrences[key].add(span.page)
            total_occurrences[key] += 1
    page_count = len(geometries)
    header_footer_keys = _header_footer_keys(spans, geometries, page_occurrences, page_count)
    kept: list[TextSpan] = []
    decisions: list[RemovedText] = []
    for index, span in enumerate(spans):
        key = normalized[index]
        if key in header_footer_keys and _is_header_footer_position(span, geometries[span.page]):
            decisions.append(_decision(span, "重复页眉/页脚", 0.98, True))
            continue
        score = _watermark_score(span, geometries[span.page], total_occurrences[key], len(page_occurrences[key]))
        removed = score >= 0.65
        if score >= 0.45:
            decisions.append(_decision(span, "文本层水印候选", score, removed))
        if not removed:
            kept.append(span)
    return FilterResult(kept=kept, removed=decisions)


def _is_header_footer_position(span: TextSpan, geometry: PageGeometry) -> bool:
    return span.bbox[1] <= geometry.height * 0.08 or span.bbox[3] >= geometry.height * 0.92


def _header_footer_keys(
    spans: Iterable[TextSpan],
    geometries: dict[int, PageGeometry],
    page_occurrences: dict[str, set[int]],
    page_count: int,
) -> set[str]:
    if page_count < 2:
        return set()
    threshold = max(2, math.ceil(page_count * 0.5))
    candidates: set[str] = set()
    for span in spans:
        geometry = geometries[span.page]
        top = span.bbox[1] <= geometry.height * 0.08
        bottom = span.bbox[3] >= geometry.height * 0.92
        key = _key(span.text)
        if (top or bottom) and len(page_occurrences[key]) >= threshold and len(key) <= 80:
            candidates.add(key)
    return candidates


def _watermark_score(span: TextSpan, geometry: PageGeometry, total: int, page_frequency: int) -> float:
    key = _key(span.text)
    score = 0.0
    if total >= 3:
        score += 0.3
    if page_frequency >= 2:
        score += 0.2
    if abs(span.direction[1]) > 0.15 or span.direction[0] < 0.95:
        score += 0.3
    center_x = (span.bbox[0] + span.bbox[2]) / 2
    center_y = (span.bbox[1] + span.bbox[3]) / 2
    if (
        geometry.width * 0.15 < center_x < geometry.width * 0.85
        and geometry.height * 0.15 < center_y < geometry.height * 0.85
    ):
        score += 0.1
    if WATERMARK_HINT.search(key):
        score += 0.3
    if len(key) > 60:
        score -= 0.2
    return min(max(score, 0.0), 1.0)


def _key(text: str) -> str:
    return re.sub(r"\s+", "", normalize_line(text)).lower()


def _decision(span: TextSpan, reason: str, score: float, removed: bool) -> RemovedText:
    return RemovedText(page=span.page, text=span.text, reason=reason, score=round(score, 3), removed=removed)
