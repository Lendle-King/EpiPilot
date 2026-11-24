"""Evidence-grounded extraction prompt and validator for paper claims and limitations."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from epipilot.topic_discovery.models import PaperAnalysis, PaperReference


def build_paper_analysis_prompt(
    paper: PaperReference,
    full_text: str,
    *,
    max_characters: int = 100_000,
) -> str:
    """Build a bounded extraction prompt; it does not call an LLM."""
    if max_characters < 1:
        raise ValueError("max_characters must be positive")
    text = full_text[:max_characters]
    digest = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    return f"""你是严谨的学术论文分析助手。请只根据给定论文文本提取结构化信息。

论文标题:{paper.title}
论文年份:{paper.year}
论文标识:{paper.id}
来源文本 SHA-256:{digest}

任务:提取论文明确提出的 claims、方法依赖的 assumptions,以及作者明确讨论或文本充分支持的
limitations。

严格规则:
1. 不得使用论文文本以外的知识补全结论。
2. 每条 statement 必须提供 evidence_locator(章节、页码或段落)。
3. 每条 statement 必须提供 supporting_quote,且必须逐字复制自输入文本。
4. Claim 应描述论文实际主张,不要把作者的动机改写成已证明结果。
5. Assumption 应区分作者明确声明的假设与方法隐含的必要条件;无法定位则不要提取。
6. Limitation 优先提取作者明确承认的局限;不要把未讨论的可能风险伪装成作者结论。
7. 证据不足时宁可返回空 statements,不要猜测。
8. confidence 是抽取置信度,不是科学主张为真的概率。
9. 返回纯 JSON,不要 Markdown 代码围栏或额外解释。

输出 JSON schema:
{{
  "paper_id": "{paper.id}",
  "source_text_sha256": "{digest}",
  "extraction_model": "model-name",
  "statements": [
    {{
      "id": "claim-1",
      "kind": "claim",
      "statement": "concise statement",
      "evidence_locator": "Section 3, paragraph 2",
      "supporting_quote": "exact verbatim quote from the supplied text",
      "confidence": 0.0
    }}
  ],
  "reviewed_by_human": false
}}

论文文本开始:
{text}
论文文本结束。
"""


def validate_paper_analysis(
    paper: PaperReference,
    raw_json: str,
    full_text: str,
) -> PaperAnalysis:
    """Parse model output and require every quoted span to occur in the pinned text."""
    analysis = PaperAnalysis.model_validate_json(raw_json)
    if analysis.paper_id != paper.id:
        raise ValueError("paper analysis references a different paper")
    expected_sha = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    if analysis.source_text_sha256 != expected_sha:
        raise ValueError("paper analysis source-text SHA-256 does not match the supplied text")

    normalized_text = _normalize_quote(full_text)
    for statement in analysis.statements:
        quote = _normalize_quote(statement.supporting_quote)
        if not quote or quote not in normalized_text:
            raise ValueError(
                f"statement {statement.id} supporting quote is not present in source text"
            )
    return analysis


def load_paper_text(path: Path, *, max_bytes: int = 2_000_000) -> str:
    """Read a bounded UTF-8 text artifact produced by the PDF extraction stage."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError("paper text artifact does not exist")
    if resolved.stat().st_size > max_bytes:
        raise ValueError("paper text artifact exceeds configured size limit")
    return resolved.read_text(encoding="utf-8")


def _normalize_quote(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
