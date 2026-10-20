"""确定性条款候选与复核规则。"""

import re
from dataclasses import dataclass
from decimal import Decimal

from smart_compliance.comparison.normalizer import (
    compact_text,
    currency_values,
    date_values,
    exception_markers,
    key_constraints,
    money_values,
    negation_markers,
    organization_names,
    percentage_values,
    quantity_values,
    time_values,
)
from smart_compliance.domain.models import Evidence, ParsedDocument, Verdict
from smart_compliance.domain.taxonomy import CLAUSE_BY_CODE, CLAUSES, ClauseDefinition


@dataclass(frozen=True, slots=True)
class ClauseCandidate:
    """来自文档分段的条款候选。"""

    clause: ClauseDefinition
    evidence: Evidence
    score: int


@dataclass(frozen=True, slots=True)
class RuleDecision:
    """确定性条款比对决策。"""

    verdict: Verdict
    difference: str
    confidence: float


MULTI_CANDIDATE_CODES = {
    "unit_price",
    "quantity",
    "service_scope",
    "value_added_service",
    "delivery_period",
    "contract_term",
    "payment_terms",
}


def collect_candidates(document: ParsedDocument) -> dict[str, ClauseCandidate]:
    """从清洗分段中提取每类条款的最佳候选。

    Args:
        document: 已解析文档。

    Returns:
        条款代码到候选证据的映射。
    """
    groups = collect_candidate_groups(document)
    return {code: max(candidates, key=lambda candidate: candidate.score) for code, candidates in groups.items()}


def collect_candidate_groups(document: ParsedDocument) -> dict[str, list[ClauseCandidate]]:
    """保留每类条款中证据不同的多个候选，而非只保留最高分。"""
    result: dict[str, list[ClauseCandidate]] = {}
    signatures: dict[str, set[str]] = {}
    for chunk in document.chunks:
        for clause in CLAUSES:
            for quote in _candidate_quotes(chunk.text, clause):
                score = _candidate_score(quote, clause)
                if score <= 0 or not _has_substance(quote, clause.code):
                    continue
                if clause.code not in MULTI_CANDIDATE_CODES and clause.code in result:
                    if score > result[clause.code][0].score:
                        result[clause.code][0] = ClauseCandidate(
                            clause=clause,
                            evidence=Evidence(quote=quote, page=chunk.page_start, chunk_id=chunk.chunk_id),
                            score=score,
                        )
                    continue
                signature = _candidate_signature(clause.code, quote)
                if signature in signatures.setdefault(clause.code, set()):
                    continue
                signatures[clause.code].add(signature)
                result.setdefault(clause.code, []).append(
                    ClauseCandidate(
                        clause=clause,
                        evidence=Evidence(quote=quote, page=chunk.page_start, chunk_id=chunk.chunk_id),
                        score=score,
                    )
                )
    for candidates in result.values():
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return result


def candidate_source_order(
    document: ParsedDocument,
    candidates: dict[str, list[ClauseCandidate]],
) -> list[ClauseCandidate]:
    """按 chunk 与证据在 chunk 内的位置排列多个采购条款候选。"""
    chunk_order = {chunk.chunk_id: index for index, chunk in enumerate(document.chunks)}
    chunk_text = {chunk.chunk_id: chunk.text for chunk in document.chunks}
    flattened = [candidate for group in candidates.values() for candidate in group]
    return sorted(
        flattened,
        key=lambda candidate: (
            chunk_order.get(candidate.evidence.chunk_id or "", len(document.chunks)),
            chunk_text.get(candidate.evidence.chunk_id or "", "").find(candidate.evidence.quote),
            candidate.clause.code,
        ),
    )


def match_candidate(source: ClauseCandidate, candidates: list[ClauseCandidate]) -> ClauseCandidate | None:
    """从同类合同候选中召回约束和文本最接近的一项。"""
    if not candidates:
        return None
    source_constraints = _clause_constraints(source.clause.code, source.evidence.quote)

    def score(candidate: ClauseCandidate) -> tuple[int, float, int]:
        contract_constraints = _clause_constraints(candidate.clause.code, candidate.evidence.quote)
        overlap = len(source_constraints & contract_constraints)
        return overlap, _character_similarity(source.evidence.quote, candidate.evidence.quote), candidate.score

    return max(candidates, key=score)


def decide_clause(source: ClauseCandidate, contract: ClauseCandidate | None) -> RuleDecision:
    """使用保守确定性规则比对一项条款。

    Args:
        source: 采购结果条款。
        contract: 合同对应条款，可能缺失。

    Returns:
        三分类结论。
    """
    if contract is None:
        return RuleDecision(Verdict.MISSING, "采购结果存在该项约定，合同未找到可核验的对应条款。", 0.88)
    source_text = source.evidence.quote
    contract_text = contract.evidence.quote
    code = source.clause.code
    special = _special_decision(code, source_text, contract_text)
    if special is not None:
        return special
    source_constraints = _clause_constraints(code, source_text)
    contract_constraints = _clause_constraints(code, contract_text)
    missing_constraints = source_constraints - contract_constraints
    if missing_constraints and code in {
        "unit_price",
        "quantity",
        "service_scope",
        "value_added_service",
        "delivery_period",
        "contract_term",
        "payment_terms",
        "tax_rate",
        "warranty_period",
        "breach_liability",
    }:
        detail = "合同未承接或改动了采购结果中的关键数值/期限约束：" + "、".join(sorted(missing_constraints))
        return RuleDecision(Verdict.DIFFERENT, detail, 0.92)
    if source_constraints and source_constraints <= contract_constraints:
        return RuleDecision(Verdict.CONSISTENT, "合同承接了采购结果中可确定核验的关键约束。", 0.9)
    similarity = _character_similarity(source_text, contract_text)
    if similarity >= 0.55:
        return RuleDecision(Verdict.CONSISTENT, "合同找到语义对应条款，未发现可由确定性规则确认的实质冲突。", 0.8)
    return RuleDecision(
        Verdict.CONSISTENT,
        "双方存在对应候选，但确定性证据不足；暂未确认实质冲突，必须人工复核。",
        0.62,
    )


def _special_decision(code: str, source_text: str, contract_text: str) -> RuleDecision | None:
    if code == "supplier":
        return _supplier_decision(source_text, contract_text)
    if code == "total_price":
        return _price_decision(source_text, contract_text)
    if bool(negation_markers(source_text)) != bool(negation_markers(contract_text)):
        return RuleDecision(Verdict.DIFFERENT, "双方条款的肯定/否定约束相反，需按原文复核责任或服务范围。", 0.94)
    if bool(exception_markers(source_text)) != bool(exception_markers(contract_text)):
        return RuleDecision(Verdict.DIFFERENT, "一方新增或删除了例外条件，可能改变条款适用范围。", 0.9)
    return None


def source_order(document: ParsedDocument, candidates: dict[str, ClauseCandidate]) -> list[str]:
    """按采购结果文本中的首次出现位置排序条款。"""
    normalized = compact_text(document.cleaned_text)
    return sorted(
        candidates,
        key=lambda code: min(
            (
                normalized.find(compact_text(keyword))
                for keyword in CLAUSE_BY_CODE[code].keywords
                if keyword in document.cleaned_text
            ),
            default=len(normalized),
        ),
    )


def _candidate_score(text: str, clause: ClauseDefinition) -> int:
    score = sum((20 + len(keyword)) * text.count(keyword) for keyword in clause.keywords)
    if clause.code == "supplier" and "有限公司" in text:
        score += 200
    if clause.code == "total_price" and ("¥" in text or "￥" in text):
        score += 100
    return score


def _candidate_quotes(text: str, clause: ClauseDefinition) -> list[str]:
    if clause.code not in MULTI_CANDIDATE_CODES:
        return [_excerpt(text, clause)] if any(keyword in text for keyword in clause.keywords) else []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    matched = [
        index
        for index, line in enumerate(lines)
        if any(keyword in line for keyword in clause.keywords) and _line_has_clause_anchor(line, clause.code)
    ]
    if len(matched) <= 1:
        return [_excerpt(text, clause)] if matched or any(keyword in text for keyword in clause.keywords) else []
    quotes: list[str] = []
    for index in matched:
        if clause.code == "quantity":
            quotes.append(lines[index][:500])
        else:
            start = max(0, index - 1)
            end = min(len(lines), index + 2)
            quotes.append("\n".join(lines[start:end])[:500])
    return quotes


def _line_has_clause_anchor(line: str, code: str) -> bool:
    if code == "payment_terms":
        return any(anchor in line for anchor in ("付款", "货款", "预付", "结算"))
    return True


def _candidate_signature(code: str, quote: str) -> str:
    constraints = _clause_constraints(code, quote)
    if constraints:
        return "|".join(sorted(constraints))
    return compact_text(quote)[:240]


def _has_substance(text: str, code: str) -> bool:
    if code == "supplier":
        return "有限公司" in text
    if code == "total_price":
        return bool(money_values(text))
    if code != "value_added_service":
        return True
    has_training = "培训" in text and any(token in text for token in ("免费", "季度", "小时"))
    has_response = "应急响应" in text and any(token in text for token in ("小时", "分钟", "7×24", "7x24"))
    return has_training or has_response


def _excerpt(text: str, clause: ClauseDefinition, maximum: int = 500) -> str:
    if clause.code == "total_price":
        money_matches = list(re.finditer(r"[¥￥]\s*([0-9][0-9,]*(?:\.\d+)?)", text))
        if money_matches:
            anchor = max(
                money_matches,
                key=lambda match: Decimal(match.group(1).replace(",", "")),
            ).start()
            start = max(0, anchor - 100)
            return text[start : min(len(text), start + 260)].strip()
    positions = [text.find(keyword) for keyword in clause.keywords if keyword in text]
    first = min(positions, default=0)
    start = max(0, first - 80)
    desired = 420 if clause.code == "value_added_service" else 240
    end = min(len(text), start + min(desired, maximum))
    return text[start:end].strip()


def _clause_constraints(code: str, text: str) -> set[str]:
    """仅提取与当前条款语义相关的确定性约束，避免跨条款数字串扰。"""
    if code in {"delivery_period", "contract_term", "warranty_period"}:
        constraints = {f"time:{value}" for value in time_values(text)}
        constraints.update(f"date:{value}" for value in date_values(text))
        return constraints
    if code in {"payment_terms", "tax_rate", "unit_price", "quantity", "value_added_service"}:
        return _special_constraints(code, text)
    constraints = key_constraints(text)
    constraints.update(f"negation:{value}" for value in negation_markers(text))
    if code == "breach_liability":
        constraints.update(_tax_basis_constraints(text))
    return constraints


def _special_constraints(code: str, text: str) -> set[str]:
    if code == "payment_terms":
        return _payment_constraints(text) | _tax_basis_constraints(text)
    if code == "tax_rate":
        constraints = {f"percent:{value}" for value in percentage_values(text)}
        return constraints | _tax_basis_constraints(text)
    if code == "unit_price":
        constraints = {f"money:{value}" for value in money_values(text)}
        constraints.update(f"currency:{value}" for value in currency_values(text))
        return constraints
    if code == "quantity":
        return {f"quantity:{value}" for value in quantity_values(text)}
    if code == "value_added_service":
        return {f"time:{value}" for value in time_values(text)}
    return set()


def _tax_basis_constraints(text: str) -> set[str]:
    constraints: set[str] = set()
    if "不含税" in text or "税前" in text:
        constraints.add("tax_basis:excluded")
    elif "含税" in text or "税后" in text:
        constraints.add("tax_basis:included")
    return constraints


def _payment_constraints(text: str) -> set[str]:
    """提取付款比例和付款时点，兼容 PDF 中数值与“日内”顺序倒置。"""
    compact = re.sub(r"\s+", "", text)
    constraints: set[str] = set()
    normal_day_pattern = re.compile(r"(?:验?收合格后).{0,16}?(\d+)\s*(个?工作日|日|天)")
    reversed_day_pattern = re.compile(r"(?:验?收合格后).{0,28}?(日内|天内).{0,28}?(\d+)")
    for value, unit in normal_day_pattern.findall(compact):
        constraints.add(f"time:{value}{unit.replace('个', '')}")
    for unit, value in reversed_day_pattern.findall(compact):
        constraints.add(f"time:{value}{unit.removesuffix('内')}")
    advance_patterns = (
        re.compile(r"预付.{0,16}?(\d+(?:\.\d+)?)\s*[%％]"),
        re.compile(r"(\d+(?:\.\d+)?)\s*[%％].{0,16}?预付"),
    )
    for pattern in advance_patterns:
        constraints.update(f"percent:{value}" for value in pattern.findall(compact))
    return constraints


def _supplier_decision(source: str, contract: str) -> RuleDecision:
    source_names = organization_names(source)
    contract_names = organization_names(contract)
    exact = source_names & contract_names
    suffix_match = any(_common_suffix_length(left, right) >= 6 for left in source_names for right in contract_names)
    if source_names and contract_names and (exact or suffix_match):
        return RuleDecision(Verdict.CONSISTENT, "中选供应商与合同乙方名称一致。", 0.96)
    return RuleDecision(Verdict.DIFFERENT, "未能确认中选供应商与合同乙方为同一主体。", 0.9)


def _price_decision(source: str, contract: str) -> RuleDecision:
    source_values = money_values(source)
    contract_values = money_values(contract)
    source_currencies = currency_values(source)
    contract_currencies = currency_values(contract)
    currency_matches = not source_currencies or not contract_currencies or bool(source_currencies & contract_currencies)
    if source_values and source_values & contract_values and currency_matches:
        return RuleDecision(Verdict.CONSISTENT, "采购结果总价与合同总价一致。", 0.98)
    return RuleDecision(Verdict.DIFFERENT, "采购结果与合同的金额未能对应。", 0.95)


def _character_similarity(source: str, contract: str) -> float:
    source_chars = set(re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", source))
    contract_chars = set(re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", contract))
    if not source_chars:
        return 0.0
    return len(source_chars & contract_chars) / len(source_chars)


def _common_suffix_length(left: str, right: str) -> int:
    count = 0
    for left_character, right_character in zip(reversed(left), reversed(right), strict=False):
        if left_character != right_character:
            break
        count += 1
    return count
