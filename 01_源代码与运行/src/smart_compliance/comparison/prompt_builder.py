"""版本化 LLM 提示词装配。"""

import json
from pathlib import Path

from smart_compliance.config import project_root
from smart_compliance.domain.models import ParsedDocument

PROMPT_VERSION = "2026-08-30.v6"

OUTPUT_SCHEMA_HINT = {
    "covered_clause_codes": ["string"],
    "items": [
        {
            "clause_code": "string",
            "verdict": "missing|different",
            "procurement_evidence": {"quote": "string", "page": 1, "chunk_id": "string"},
            "contract_evidence": {"quote": "string", "page": "integer|null", "chunk_id": "string|null"},
            "difference": "string",
            "confidence": 0.0,
            "needs_human_review": False,
        }
    ],
    "limitations": ["string"],
}


class PromptBuilder:
    """从项目 prompts 目录构建模型消息。"""

    def __init__(self, prompt_dir: Path | None = None) -> None:
        """初始化提示词构建器。

        Args:
            prompt_dir: 提示词目录；默认使用项目 prompts。
        """
        self._prompt_dir = prompt_dir or project_root() / "prompts"

    def build_messages(self, procurement: ParsedDocument, contract: ParsedDocument) -> list[dict[str, str]]:
        """构建 OpenAI-compatible 消息。

        Args:
            procurement: 采购结果解析文本。
            contract: 采购合同解析文本。

        Returns:
            system/user 消息列表。
        """
        if procurement.ocr_source != "page_image" or contract.ocr_source != "page_image":
            raise ValueError("外部模型输入必须来自 PDF 页面图片 OCR，禁止使用文本层。")
        if not procurement.ocr_text_sha256 or not contract.ocr_text_sha256:
            raise ValueError("页面图片 OCR 输入缺少文本哈希。")
        system_prompt = (self._prompt_dir / "contract_review_system.md").read_text(encoding="utf-8")
        user_template = (self._prompt_dir / "contract_review_user.md").read_text(encoding="utf-8")
        examples = json.loads((self._prompt_dir / "few_shot_examples.json").read_text(encoding="utf-8"))
        values = {
            "schema": json.dumps(OUTPUT_SCHEMA_HINT, ensure_ascii=False, separators=(",", ":")),
            "examples": json.dumps(examples, ensure_ascii=False, separators=(",", ":")),
            "procurement_chunks": json.dumps(
                [chunk.model_dump() for chunk in procurement.chunks], ensure_ascii=False, separators=(",", ":")
            ),
            "contract_chunks": json.dumps(
                [chunk.model_dump() for chunk in contract.chunks], ensure_ascii=False, separators=(",", ":")
            ),
        }
        user_prompt = user_template
        for name, value in values.items():
            user_prompt = user_prompt.replace("{" + name + "}", value)
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
