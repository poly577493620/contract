"""版本化提示词装配。"""

import json

from smart_compliance.comparison.prompt_builder import PROMPT_VERSION, PromptBuilder
from smart_compliance.config import project_root
from smart_compliance.domain.models import DocumentRole
from tests.conftest import parsed_document


def test_prompt_builder_preserves_literal_json_and_replaces_placeholders() -> None:
    """模板中的 missing 证据 JSON 不得被误当成 format 占位符。"""
    procurement = parsed_document("付款方式：验收合格后15日内付款", DocumentRole.PROCUREMENT_RESULT, "005_采购结果.pdf")
    contract = parsed_document("付款方式：验收合格后30日内付款", DocumentRole.CONTRACT, "005_采购合同.pdf")
    messages = PromptBuilder().build_messages(procurement, contract)
    assert PROMPT_VERSION == "2026-08-30.v6"
    assert len(messages) == 2
    user_prompt = messages[1]["content"]
    assert '{"quote":"","page":null,"chunk_id":null}' in user_prompt
    assert "{schema}" not in user_prompt
    assert "result-p1-c1" in user_prompt
    assert "contract-p1-c1" in user_prompt
    assert "covered_clause_codes" in user_prompt
    assert "set(items[*].clause_code) ⊆ set(covered_clause_codes)" in user_prompt
    assert "每条问题编码已先列入 covered_clause_codes" in user_prompt
    assert "连续原样子串" in user_prompt
    assert "procurement_ocr_chunks" in user_prompt


def test_structured_few_shot_item_codes_are_covered() -> None:
    """结构化示例中的每个问题编码都必须先列入覆盖清单。"""
    path = project_root() / "prompts" / "few_shot_examples.json"
    examples = json.loads(path.read_text(encoding="utf-8"))
    structured_examples = [example["output"] for example in examples if "output" in example]
    assert structured_examples
    for output in structured_examples:
        covered_codes = set(output["covered_clause_codes"])
        item_codes = {item["clause_code"] for item in output["items"]}
        assert item_codes <= covered_codes


def test_prompt_templates_do_not_contain_sample_specific_rules() -> None:
    """生产提示词不得包含目标样本编号或 Gold 特判。"""
    prompt_dir = project_root() / "prompts"
    prompt_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(prompt_dir.glob("*")))
    assert 'sample_id == "006"' not in prompt_text
    assert '"sample_id":"006"' not in prompt_text
    assert "7f3cc23fc4da85619edd4a90e2cd220205d4af9d302896172c53918c4200c374" not in prompt_text
