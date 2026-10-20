"""PDF 文本清洗和分段。"""

import re
from collections.abc import Iterable

from smart_compliance.domain.models import TextChunk

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MULTIPLE_SPACES = re.compile(r"[ \t\u3000]+")
MULTIPLE_BLANK_LINES = re.compile(r"\n{3,}")
SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；])")


def normalize_line(text: str) -> str:
    """规范化单行文本。

    Args:
        text: 原始文本。

    Returns:
        去除控制字符和异常空格后的文本。
    """
    cleaned = CONTROL_CHARACTERS.sub("", text).replace("\ufeff", "").replace("�", "")
    return MULTIPLE_SPACES.sub(" ", cleaned).strip()


def clean_page_lines(lines: Iterable[str]) -> str:
    """把页面文本行合并为清晰段落。

    Args:
        lines: 按阅读顺序排列的文本行。

    Returns:
        清洗后的页面文本。
    """
    normalized = [normalize_line(line) for line in lines]
    non_empty = [line for line in normalized if line]
    paragraphs: list[str] = []
    for line in non_empty:
        if paragraphs and _should_join(paragraphs[-1], line):
            paragraphs[-1] = _join_lines(paragraphs[-1], line)
        else:
            paragraphs.append(line)
    return MULTIPLE_BLANK_LINES.sub("\n\n", "\n".join(paragraphs)).strip()


def _should_join(previous: str, current: str) -> bool:
    if previous.endswith(("。", "！", "？", "；", ":", "：")):
        return False
    if re.match(r"^(第[一二三四五六七八九十百0-9]+[条章节]|[（(]?[0-9一二三四五六七八九十]+[）).、])", current):
        return False
    return len(previous) < 80 and len(current) < 80


def _join_lines(previous: str, current: str) -> str:
    if previous.endswith("-") and current[:1].isascii():
        return previous[:-1] + current
    if previous[-1:].isascii() and current[:1].isascii():
        return f"{previous} {current}"
    return previous + current


def build_chunks(page_texts: list[str], prefix: str, max_characters: int = 1200) -> list[TextChunk]:
    """按页和句子构建稳定分段。

    Args:
        page_texts: 每页清洗文本。
        prefix: chunk ID 前缀。
        max_characters: 单分段最大字符数。

    Returns:
        可回溯文本分段。
    """
    chunks: list[TextChunk] = []
    for page_number, page_text in enumerate(page_texts, start=1):
        page_chunks = _split_text(page_text, max_characters)
        for index, text in enumerate(page_chunks, start=1):
            chunks.append(
                TextChunk(
                    chunk_id=f"{prefix}-p{page_number}-c{index}",
                    page_start=page_number,
                    page_end=page_number,
                    text=text,
                )
            )
    return chunks


def _split_text(text: str, max_characters: int) -> list[str]:
    if not text:
        return []
    units = [
        part
        for line in text.splitlines()
        for unit in SENTENCE_BOUNDARY.split(line)
        for part in _hard_split(unit.strip(), max_characters)
        if part
    ]
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 1 > max_characters:
            chunks.append(current)
            current = unit
        else:
            current = f"{current}\n{unit}".strip()
    if current:
        chunks.append(current)
    return chunks


def _hard_split(text: str, max_characters: int) -> list[str]:
    return [text[start : start + max_characters] for start in range(0, len(text), max_characters)]
