"""RapidOCR 的窄接口与可测试识别结果。"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol, cast

from rapidocr import RapidOCR


@dataclass(frozen=True, slots=True)
class RawOcrFragment:
    """OCR 引擎返回的原始文字与矩形框。"""

    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


class OcrEngine(Protocol):
    """页面 OCR 引擎最小协议。"""

    @property
    def name(self) -> str:
        """返回引擎名称。"""
        ...

    @property
    def version(self) -> str:
        """返回引擎包版本。"""
        ...

    @property
    def model_fingerprint(self) -> str:
        """返回本地模型文件指纹。"""
        ...

    def recognize(self, image_path: Path, text_score: float) -> list[RawOcrFragment]:
        """识别一张页面图片。"""
        ...


class RapidOcrEngine:
    """使用 ONNX Runtime CPU 的本地 RapidOCR 适配器。"""

    def __init__(self) -> None:
        """延迟初始化模型，缓存命中时不加载 ONNX 会话。"""
        self._engine: RapidOCR | None = None
        self._version = version("rapidocr")
        self._model_fingerprint = _rapidocr_model_fingerprint()

    @property
    def name(self) -> str:
        """返回引擎名称。"""
        return "RapidOCR-ONNXRuntime-CPU"

    @property
    def version(self) -> str:
        """返回 RapidOCR 版本。"""
        return self._version

    @property
    def model_fingerprint(self) -> str:
        """返回打包 ONNX 模型文件的组合指纹。"""
        return self._model_fingerprint

    def recognize(self, image_path: Path, text_score: float) -> list[RawOcrFragment]:
        """识别页面并只返回达到最低文字置信度的片段。"""
        if self._engine is None:
            self._engine = RapidOCR()
        result = self._engine(image_path, text_score=text_score)
        boxes = cast(Sequence[Any] | None, getattr(result, "boxes", None))
        texts = cast(Sequence[str] | None, getattr(result, "txts", None))
        scores = cast(Sequence[float] | None, getattr(result, "scores", None))
        if boxes is None or texts is None or scores is None:
            return []
        fragments: list[RawOcrFragment] = []
        for box, text, score in zip(boxes, texts, scores, strict=True):
            normalized = str(text).strip()
            if not normalized:
                continue
            points = cast(Sequence[Sequence[float]], box)
            x_values = [float(point[0]) for point in points]
            y_values = [float(point[1]) for point in points]
            fragments.append(
                RawOcrFragment(
                    text=normalized,
                    bbox=(min(x_values), min(y_values), max(x_values), max(y_values)),
                    confidence=max(0.0, min(1.0, float(score))),
                )
            )
        return fragments


def _rapidocr_model_fingerprint() -> str:
    spec = find_spec("rapidocr")
    if spec is None or spec.origin is None:
        return "unavailable"
    model_dir = Path(spec.origin).resolve().parent / "models"
    model_files = sorted(model_dir.glob("*.onnx"))
    digest = hashlib.sha256()
    for model_path in model_files:
        digest.update(model_path.name.encode("utf-8"))
        with model_path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()
