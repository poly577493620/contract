"""OCR 坐标阅读顺序恢复。"""

from smart_compliance.parsing.ocr_engine import RawOcrFragment
from smart_compliance.parsing.reading_order import order_page_fragments


def _fragment(text: str, x: float, y: float) -> RawOcrFragment:
    return RawOcrFragment(text, (x, y, x + 80, y + 8), 0.98)


def test_two_columns_are_not_interleaved() -> None:
    """双栏页面应先读完左栏，再读右栏。"""
    fragments = [
        _fragment("左一", 20, 10),
        _fragment("左二", 20, 30),
        _fragment("左三", 20, 50),
        _fragment("左四", 20, 70),
        _fragment("右一", 320, 20),
        _fragment("右二", 320, 40),
        _fragment("右三", 320, 60),
        _fragment("右四", 320, 80),
    ]
    page = order_page_fragments(fragments, page=2, page_width=600, image_sha256="a" * 64)
    assert page.column_mode == "multi"
    assert page.lines == ["左一", "左二", "左三", "左四", "右一", "右二", "右三", "右四"]
    assert [span.reading_order for span in page.spans] == list(range(1, 9))


def test_table_cells_stay_in_row_order() -> None:
    """左右单元格垂直对齐时应按表格逐行读取。"""
    fragments = [
        _fragment("字段A", 20, 10),
        _fragment("值A", 320, 10),
        _fragment("字段B", 20, 30),
        _fragment("值B", 320, 30),
        _fragment("字段C", 20, 50),
        _fragment("值C", 320, 50),
        _fragment("字段D", 20, 70),
        _fragment("值D", 320, 70),
    ]
    page = order_page_fragments(fragments, page=1, page_width=600, image_sha256="b" * 64)
    assert page.column_mode == "table_like"
    assert page.lines[0] == "字段A\t值A"
    assert page.lines[-1] == "字段D\t值D"
