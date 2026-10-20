"""安全解压教学数据集，并修复未设置 UTF-8 标志的 ZIP 文件名。"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = PROJECT_ROOT / "智审合规助手数据集.zip"
TARGET = PROJECT_ROOT / "data" / "raw"


def _decoded_name(name: str) -> str:
    """修复被 ZIP CP437 默认规则误解码的 UTF-8 文件名。"""
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _is_metadata(parts: tuple[str, ...]) -> bool:
    """判断归档条目是否为 macOS 元数据。"""
    return "__MACOSX" in parts or ".DS_Store" in parts or any(part.startswith("._") for part in parts)


def extract_dataset(archive: Path = ARCHIVE, target: Path = TARGET) -> tuple[int, int]:
    """把数据集解压到项目目录内。

    Args:
        archive: ZIP 文件路径。
        target: 解压目标目录。

    Returns:
        `(文件数, PDF 数)`。

    Raises:
        ValueError: 目标越过项目目录或归档含路径穿越条目。
    """
    root = PROJECT_ROOT.resolve()
    destination_root = target.resolve()
    if not destination_root.is_relative_to(root):
        message = f"目标目录必须位于项目内: {destination_root}"
        raise ValueError(message)
    destination_root.mkdir(parents=True, exist_ok=True)
    file_count = 0
    pdf_count = 0
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            parts = PurePosixPath(_decoded_name(info.filename)).parts
            if not parts or _is_metadata(parts):
                continue
            output = destination_root.joinpath(*parts).resolve()
            if not output.is_relative_to(destination_root):
                message = f"归档条目越过目标目录: {info.filename}"
                raise ValueError(message)
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, output.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            file_count += 1
            pdf_count += output.suffix.lower() == ".pdf"
    return file_count, pdf_count


if __name__ == "__main__":
    extracted_files, extracted_pdfs = extract_dataset()
    raise SystemExit(0 if extracted_files > 0 and extracted_pdfs > 0 else 1)
