"""领域异常。"""


class SmartComplianceError(RuntimeError):
    """应用可向用户解释的基础异常。"""


class PdfValidationError(SmartComplianceError):
    """PDF 输入校验失败。"""


class PdfParsingError(SmartComplianceError):
    """PDF 内容解析失败。"""


class OcrQualityError(PdfParsingError):
    """页面图片 OCR 连续两次未通过质量门禁。"""


class ModelConfigurationError(SmartComplianceError):
    """模型端点未配置或配置无效。"""


class ModelInvocationError(SmartComplianceError):
    """模型调用失败。"""

    def __init__(
        self,
        message: str,
        *,
        category: str = "model_invocation_error",
        status_code: int | None = None,
        retry_count: int = 0,
        elapsed_ms: int | None = None,
    ) -> None:
        """初始化不包含响应正文或密钥的可审计错误。"""
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retry_count = retry_count
        self.elapsed_ms = elapsed_ms
        self.diagnostics: dict[str, object] = {}
        self.attempts: list[dict[str, object]] = []


class ModelOutputError(SmartComplianceError):
    """模型输出无法通过结构校验。"""

    def __init__(self, message: str) -> None:
        """初始化可由调用编排补充运行信息的输出错误。"""
        super().__init__(message)
        self.retry_count = 0
        self.elapsed_ms: int | None = None


class BlindSetViolationError(SmartComplianceError):
    """评测流程试图把盲测集当作有标签数据使用。"""
