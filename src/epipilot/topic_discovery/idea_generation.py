"""Diverse, evidence-linked research idea generation contracts and validators."""

from __future__ import annotations

import hashlib
import json
import re
from itertools import combinations

from epipilot.topic_discovery.models import TopicSpec
from epipilot.topic_discovery.v02_models import IdeaBatch, ResearchSynthesis


_STOPWORDS = {
    "a",
    "an",
    "and",
    "approach",
    "based",
    "by",
    "for",
    "from",
    "framework",
    "in",
    "into",
    "learning",
    "method",
    "model",
    "new",
    "of",
    "on",
    "or",
    "research",
    "skill",
    "system",
    "task",
    "the",
    "through",
    "to",
    "using",
    "with",
}


def build_idea_generation_prompt(
    topic: TopicSpec,
    synthesis: ResearchSynthesis,
    *,
    target_count: int = 6,
) -> str:
    """Ask a model for 5-8 materially different, falsifiable candidate projects."""
    if not 5 <= target_count <= 8:
        raise ValueError("target_count must be between 5 and 8")
    if synthesis.topic_id != topic.id:
        raise ValueError("research synthesis belongs to a different topic")
    digest = hashlib.sha256(f"{topic.id}:{synthesis.id}:{target_count}".encode()).hexdigest()[:16]
    context = {
        "topic": topic.model_dump(mode="json"),
        "synthesis": synthesis.model_dump(mode="json"),
    }
    return f"""你是顶会级别的研究选题设计者。请基于已验证来源的 Research Synthesis
生成 {target_count} 个真正不同的研究课题候选。

目标不是生成漂亮标题,而是提出可被实验推翻、且有机会形成实质性学术贡献的研究假说。

多样性要求:
1. 至少覆盖三种 novelty_dimension: problem, method, mechanism, scenario, insight。
2. 不允许只更换数据集、模型名称、任务措辞或 benchmark 就算一个新方向。
3. 每个候选必须有独立的核心机制或研究问题;尽量让候选来自不同 gap。
4. 每个候选都必须给出 alternative_explanations 和 falsification_conditions。
5. 明确区分“问题重要”与“方法原创”;不要因为 gap 看起来重要就推断方法新颖。
6. 候选必须引用 supporting_evidence_ids;若存在反例,引用 counterevidence_evidence_ids。
7. 只使用 synthesis 中已有的 gap ID 和 evidence ID,不得发明引用。
8. 对证据薄弱或被相邻工作覆盖的候选,应主动降低主张强度,而不是包装成突破。

优先考虑具有 Oral 潜力的特质:
- 新的问题表述或此前未被识别的机制,而不只是增量模块。
- 能够解释现有方法失败或成功边界的可检验理论/机制。
- 有清晰的反事实对照和区分性实验。
- 能在合理资源预算内验证核心假说。
- 对相邻领域有迁移价值,但不夸大泛化范围。

每个 candidate 的字段:
- id, gap_id, title, research_question, hypothesis, proposed_mechanism,
  expected_contribution, alternative_explanations, falsification_conditions,
  novelty_dimension, supporting_evidence_ids, counterevidence_evidence_ids。

返回纯 JSON:
{{
  "id": "ideas-{digest}",
  "topic_id": "{topic.id}",
  "synthesis_id": "{synthesis.id}",
  "target_count": {target_count},
  "candidates": [
    {{
      "id": "idea-1",
      "gap_id": "existing-gap-id",
      "title": "specific research title",
      "research_question": "one precise research question",
      "hypothesis": "a falsifiable statement",
      "proposed_mechanism": "why the expected effect should occur",
      "expected_contribution": "what reusable knowledge or capability is contributed",
      "alternative_explanations": ["a plausible competing explanation"],
      "falsification_conditions": ["an observable result that rejects the hypothesis"],
      "novelty_dimension": "mechanism",
      "supporting_evidence_ids": ["paper-id::statement-id"],
      "counterevidence_evidence_ids": []
    }}
  ],
  "generation_notes": ["important caveats"]
}}

研究材料开始:
{json.dumps(context, ensure_ascii=False, indent=2)}
研究材料结束。
"""


def validate_idea_batch(
    topic: TopicSpec,
    synthesis: ResearchSynthesis,
    raw_json: str,
    *,
    similarity_threshold: float = 0.82,
) -> IdeaBatch:
    """Check evidence lineage, gap membership, diversity and near-duplicate ideas."""
    if not 0 < similarity_threshold <= 1:
        raise ValueError("similarity_threshold must be in (0, 1]")
    batch = IdeaBatch.model_validate_json(raw_json)
    if batch.topic_id != topic.id or synthesis.topic_id != topic.id:
        raise ValueError("idea batch and synthesis must belong to the supplied topic")
    if batch.synthesis_id != synthesis.id:
        raise ValueError("idea batch references a different synthesis")

    gaps = {gap.id: gap for gap in synthesis.gaps}
    evidence_ids = {item.id for item in synthesis.evidence_index}
    for candidate in batch.candidates:
        gap = gaps.get(candidate.gap_id)
        if gap is None:
            raise ValueError(f"idea {candidate.id} references unknown gap {candidate.gap_id}")
        supporting = set(candidate.supporting_evidence_ids)
        counter = set(candidate.counterevidence_evidence_ids)
        if not supporting.issubset(set(gap.supporting_evidence_ids)):
            raise ValueError(
                f"idea {candidate.id} uses evidence outside its gap's supporting evidence"
            )
        if not counter.issubset(set(gap.counterevidence_evidence_ids)):
            raise ValueError(f"idea {candidate.id} uses evidence outside its gap's counterevidence")
        if not supporting.issubset(evidence_ids) or not counter.issubset(evidence_ids):
            raise ValueError(f"idea {candidate.id} references unknown evidence")

    duplicate_pairs: list[tuple[str, str, float]] = []
    for left, right in combinations(batch.candidates, 2):
        similarity = _idea_similarity(
            left.title,
            left.proposed_mechanism,
            right.title,
            right.proposed_mechanism,
        )
        if similarity >= similarity_threshold:
            duplicate_pairs.append((left.id, right.id, similarity))
    if duplicate_pairs:
        left_id, right_id, similarity = duplicate_pairs[0]
        raise ValueError(
            "near-duplicate ideas detected: "
            f"{left_id} and {right_id} (lexical Jaccard={similarity:.3f}); "
            "revise the mechanism or research question"
        )
    return batch


def _idea_similarity(
    left_title: str,
    left_mechanism: str,
    right_title: str,
    right_mechanism: str,
) -> float:
    left = _idea_tokens(f"{left_title} {left_mechanism}")
    right = _idea_tokens(f"{right_title} {right_mechanism}")
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _idea_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]{3,}", value.casefold(), flags=re.UNICODE)
        if token not in _STOPWORDS
    }
