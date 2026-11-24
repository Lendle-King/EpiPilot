"""Evidence-grounded synthesis prompts and validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import TypeAdapter

from epipilot.topic_discovery.models import PaperAnalysis, PaperReference, TopicSpec
from epipilot.topic_discovery.v02_models import (
    EvidenceStatementRef,
    ResearchSynthesis,
)


def build_evidence_index(
    papers: Sequence[PaperReference],
    analyses: Sequence[PaperAnalysis],
) -> list[EvidenceStatementRef]:
    """Create stable paper-qualified IDs for all quote-verified statements."""
    paper_by_id = {paper.id: paper for paper in papers}
    if len(paper_by_id) != len(papers):
        raise ValueError("paper IDs must be unique before building an evidence index")
    analysis_by_id = {analysis.paper_id: analysis for analysis in analyses}
    if len(analysis_by_id) != len(analyses):
        raise ValueError("there must be at most one paper analysis per paper")
    unknown = set(analysis_by_id) - set(paper_by_id)
    if unknown:
        raise ValueError(f"paper analyses reference unknown papers: {sorted(unknown)}")

    evidence: list[EvidenceStatementRef] = []
    for analysis in analyses:
        for statement in analysis.statements:
            evidence.append(
                EvidenceStatementRef(
                    id=f"{analysis.paper_id}::{statement.id}",
                    paper_id=analysis.paper_id,
                    statement_id=statement.id,
                    kind=statement.kind,
                    evidence_locator=statement.evidence_locator,
                    supporting_quote=statement.supporting_quote,
                )
            )
    if not evidence:
        raise ValueError("at least one quote-verified paper statement is required")
    return evidence


def build_research_synthesis_prompt(
    topic: TopicSpec,
    papers: Sequence[PaperReference],
    analyses: Sequence[PaperAnalysis],
    *,
    max_evidence_statements: int = 120,
) -> str:
    """Build a bounded prompt for cross-paper themes, tensions and gap hypotheses."""
    if max_evidence_statements < 1:
        raise ValueError("max_evidence_statements must be positive")
    evidence = build_evidence_index(papers, analyses)
    if len(evidence) > max_evidence_statements:
        raise ValueError(
            "evidence index exceeds the configured prompt limit; split the synthesis "
            "into smaller topic subareas rather than silently dropping evidence"
        )
    paper_by_id = {paper.id: paper for paper in papers}
    source_ids = [analysis.paper_id for analysis in analyses]
    source_hashes = {
        analysis.paper_id: analysis.source_text_sha256 for analysis in analyses
    }
    digest = hashlib.sha256(
        f"{topic.id}:{'|'.join(sorted(source_ids))}".encode("utf-8")
    ).hexdigest()[:16]
    source_metadata = [
        {
            "paper_id": paper_id,
            "title": paper_by_id[paper_id].title,
            "year": paper_by_id[paper_id].year,
            "venue": paper_by_id[paper_id].venue,
            "abstract": (paper_by_id[paper_id].abstract or "")[:3000],
        }
        for paper_id in source_ids
    ]
    context = {
        "topic": topic.model_dump(mode="json"),
        "source_papers": source_metadata,
        "source_text_sha256": source_hashes,
        "evidence_index": [item.model_dump(mode="json") for item in evidence],
    }
    return f"""你是严格的系统性文献综合研究员。请从给定的跨论文证据中提炼研究领域结构。

研究主题: {topic.title}
研究范围: {topic.description}
研究领域: {topic.research_area}
Synthesis ID: synthesis-{digest}

目标:
1. 归纳 3-8 个有区分度的研究主题,解释它们之间的关系。
2. 找出不同论文之间真正存在的机制差异、假设冲突、评价缺口和未解决问题。
3. 提出 1-5 个 provisional research gap hypotheses。研究空白是待验证假说,不是“文献没有做过”的断言。
4. 每个 gap 必须引用 supporting_evidence_ids;若存在反例、相邻工作或相反结果,必须放入 counterevidence_evidence_ids。
5. supporting_source_ids 和 counterevidence_source_ids 必须分别精确等于对应 evidence IDs 的 paper_id 集合。
6. 不得把论文未提及某事解释为该领域从未研究该问题。
7. 证据覆盖不足时,写入 coverage_limitations 和 unresolved_questions,不得补造事实。

证据规则:
- 只能使用 evidence_index 中的 ID 和原文。
- 不得修改、重写或创造 evidence_index 项;响应中不要输出 evidence_index,验证器会从输入 analyses 注入不可变索引。
- 每个 theme 至少引用一个 evidence ID。
- 不要把同一条证据同时作为 gap 的支持与反证。
- Claim、Assumption、Limitation 必须区分;作者的动机不等于实验结论。
- 研究空白的可信度不等于科学假说为真的概率。

返回纯 JSON,结构如下:
{{
  "id": "synthesis-{digest}",
  "topic_id": "{topic.id}",
  "source_paper_ids": {json.dumps(source_ids, ensure_ascii=False)},
  "source_text_sha256": {json.dumps(source_hashes, ensure_ascii=False)},
  "themes": [
    {{
      "id": "theme-1",
      "label": "short theme label",
      "summary": "what the evidence collectively says",
      "evidence_ids": ["paper-id::statement-id"],
      "unresolved_tensions": ["a specific tension or empty list"]
    }}
  ],
  "gaps": [
    {{
      "id": "gap-1",
      "statement": "a precise and bounded gap hypothesis",
      "importance_rationale": "why resolving it matters",
      "why_unresolved": "what current evidence does not establish",
      "supporting_source_ids": ["paper-id"],
      "counterevidence_source_ids": [],
      "supporting_evidence_ids": ["paper-id::statement-id"],
      "counterevidence_evidence_ids": []
    }}
  ],
  "unresolved_questions": ["questions that need more evidence"],
  "coverage_limitations": ["limits of this search and evidence set"]
}}

研究材料开始:
{json.dumps(context, ensure_ascii=False, indent=2)}
研究材料结束。
"""


def validate_research_synthesis(
    topic: TopicSpec,
    papers: Sequence[PaperReference],
    analyses: Sequence[PaperAnalysis],
    raw_json: str,
) -> ResearchSynthesis:
    """Validate topic, source hashes and every evidence reference in a model synthesis."""
    payload = json.loads(raw_json)
    if not isinstance(payload, dict):
        raise ValueError("research synthesis response must be a JSON object")
    expected_evidence = build_evidence_index(papers, analyses)
    supplied_index = payload.get("evidence_index")
    if supplied_index is not None:
        parsed_index = TypeAdapter(list[EvidenceStatementRef]).validate_python(supplied_index)
        if parsed_index != expected_evidence:
            raise ValueError("research synthesis changed or omitted the immutable evidence index")
    payload["evidence_index"] = [
        item.model_dump(mode="json") for item in expected_evidence
    ]
    synthesis = ResearchSynthesis.model_validate(payload)
    if synthesis.topic_id != topic.id:
        raise ValueError("research synthesis references a different topic")
    expected_ids = {analysis.paper_id for analysis in analyses}
    if set(synthesis.source_paper_ids) != expected_ids:
        raise ValueError("research synthesis source paper IDs do not match supplied analyses")
    expected_hashes = {
        analysis.paper_id: analysis.source_text_sha256 for analysis in analyses
    }
    if synthesis.source_text_sha256 != expected_hashes:
        raise ValueError("research synthesis source-text hashes do not match supplied analyses")
    if synthesis.evidence_index != expected_evidence:
        raise ValueError("research synthesis changed or omitted the immutable evidence index")
    return synthesis
