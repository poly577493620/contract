"""根据 OCR 坐标恢复单栏、多栏和表格型页面的阅读顺序。"""

from dataclasses import dataclass
from statistics import median
from typing import Literal

from smart_compliance.domain.models import OcrTextSpan
from smart_compliance.parsing.ocr_engine import RawOcrFragment


@dataclass(frozen=True, slots=True)
class OrderedPage:
    """一页经阅读顺序重排的 OCR 结果。"""

    page: int
    spans: list[OcrTextSpan]
    lines: list[str]
    column_mode: Literal["single", "multi", "table_like"]


def order_page_fragments(
    fragments: list[RawOcrFragment],
    *,
    page: int,
    page_width: float,
    image_sha256: str,
) -> OrderedPage:
    """把 OCR 片段按版面坐标排列并分配稳定行号。"""
    if not fragments:
        return OrderedPage(page, [], [], "single")
    boundary = _column_boundary(fragments, page_width)
    rows = _group_rows(fragments)
    table_like = boundary is not None and _is_table_like(rows, boundary)
    if boundary is None or table_like:
        ordered_rows = rows
        mode: Literal["single", "multi", "table_like"] = "table_like" if table_like else "single"
    else:
        ordered_rows = _multi_column_rows(fragments, boundary)
        mode = "multi"
    spans: list[OcrTextSpan] = []
    lines: list[str] = []
    reading_order = 1
    for line_number, row in enumerate(ordered_rows, start=1):
        ordered_fragments = sorted(row, key=lambda item: item.bbox[0])
        lines.append(_join_row(ordered_fragments))
        for fragment in ordered_fragments:
            spans.append(
                OcrTextSpan(
                    page=page,
                    text=fragment.text,
                    bbox=fragment.bbox,
                    confidence=fragment.confidence,
                    line_id=f"p{page}-l{line_number}",
                    reading_order=reading_order,
                    page_image_sha256=image_sha256,
                )
            )
            reading_order += 1
    return OrderedPage(page, spans, lines, mode)


def _group_rows(fragments: list[RawOcrFragment]) -> list[list[RawOcrFragment]]:
    ordered = sorted(fragments, key=lambda item: (_center_y(item), item.bbox[0]))
    rows: list[list[RawOcrFragment]] = []
    for fragment in ordered:
        match = _best_row(fragment, rows)
        if match is None:
            rows.append([fragment])
        else:
            match.append(fragment)
    rows.sort(key=lambda row: min(item.bbox[1] for item in row))
    return rows


def _best_row(
    fragment: RawOcrFragment,
    rows: list[list[RawOcrFragment]],
) -> list[RawOcrFragment] | None:
    candidates: list[tuple[float, list[RawOcrFragment]]] = []
    for row in rows:
        top = min(item.bbox[1] for item in row)
        bottom = max(item.bbox[3] for item in row)
        overlap = max(0.0, min(bottom, fragment.bbox[3]) - max(top, fragment.bbox[1]))
        smaller_height = min(max(1.0, bottom - top), _height(fragment))
        center_distance = abs((top + bottom) / 2 - _center_y(fragment))
        if overlap / smaller_height >= 0.35 or center_distance <= max(_height(fragment), bottom - top) * 0.55:
            candidates.append((center_distance, row))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def _column_boundary(fragments: list[RawOcrFragment], page_width: float) -> float | None:
    if len(fragments) < 8:
        return None
    candidates = [page_width * ratio / 20 for ratio in range(7, 14)]
    choices: list[tuple[int, float]] = []
    for boundary in candidates:
        left = sum(fragment.bbox[2] < boundary for fragment in fragments)
        right = sum(fragment.bbox[0] > boundary for fragment in fragments)
        crossing = len(fragments) - left - right
        if left >= 3 and right >= 3 and crossing / len(fragments) <= 0.12:
            choices.append((crossing, boundary))
    return min(choices, default=(0, 0.0))[1] or None


def _is_table_like(rows: list[list[RawOcrFragment]], boundary: float) -> bool:
    paired_rows = 0
    eligible_rows = 0
    for row in rows:
        has_left = any(item.bbox[2] < boundary for item in row)
        has_right = any(item.bbox[0] > boundary for item in row)
        if has_left or has_right:
            eligible_rows += 1
        if has_left and has_right:
            paired_rows += 1
    return eligible_rows > 0 and paired_rows / eligible_rows >= 0.35


def _multi_column_rows(fragments: list[RawOcrFragment], boundary: float) -> list[list[RawOcrFragment]]:
    left = [fragment for fragment in fragments if fragment.bbox[2] < boundary]
    right = [fragment for fragment in fragments if fragment.bbox[0] > boundary]
    crossing = [fragment for fragment in fragments if fragment not in left and fragment not in right]
    column_top = min((_center_y(item) for item in left + right), default=0.0)
    column_bottom = max((_center_y(item) for item in left + right), default=0.0)
    before = [item for item in crossing if _center_y(item) < column_top]
    after = [item for item in crossing if _center_y(item) >= column_bottom]
    middle = [item for item in crossing if item not in before and item not in after]
    return [
        *_group_rows(before),
        *_group_rows(left),
        *_group_rows(middle),
        *_group_rows(right),
        *_group_rows(after),
    ]


def _join_row(row: list[RawOcrFragment]) -> str:
    if not row:
        return ""
    heights = [_height(item) for item in row]
    typical_height = median(heights)
    parts = [row[0].text]
    previous = row[0]
    for current in row[1:]:
        gap = current.bbox[0] - previous.bbox[2]
        if gap > typical_height * 1.5:
            separator = "\t"
        elif previous.text[-1:].isascii() and current.text[:1].isascii():
            separator = " "
        else:
            separator = ""
        parts.extend([separator, current.text])
        previous = current
    return "".join(parts).strip()


def _center_y(fragment: RawOcrFragment) -> float:
    return (fragment.bbox[1] + fragment.bbox[3]) / 2


def _height(fragment: RawOcrFragment) -> float:
    return max(1.0, fragment.bbox[3] - fragment.bbox[1])
