"""Standalone CLI for literature search, synthesis prompts and prior-art challenge."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from epipilot.topic_discovery.idea_generation import build_idea_generation_prompt
from epipilot.topic_discovery.landscape import build_research_landscape, search_report_summary
from epipilot.topic_discovery.models import (
    IdeaCandidate,
    LiteratureProvider,
    LiteratureSearchQuery,
    PaperAnalysis,
    PaperReference,
    TopicSpec,
)
from epipilot.topic_discovery.prior_art import (
    PriorArtChallengeService,
    build_prior_art_challenge_prompt,
)
from epipilot.topic_discovery.providers import SearchProvider, build_default_providers
from epipilot.topic_discovery.service import LiteratureSearchService
from epipilot.topic_discovery.synthesis import build_research_synthesis_prompt
from epipilot.topic_discovery.v02_models import ResearchSynthesis


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epidiscover",
        description="Evidence-grounded topic-level literature discovery",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="search scholarly sources and build a landscape")
    search.add_argument("--query", required=True)
    search.add_argument("--research-area", default="Computer Science")
    search.add_argument("--topic-title")
    search.add_argument("--topic-description")
    search.add_argument("--year-from", type=int)
    search.add_argument("--year-to", type=int)
    search.add_argument("--venue")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0)
    _add_provider_argument(search)
    search.add_argument(
        "--output",
        type=Path,
        default=Path(".topic-discovery") / "landscape.json",
    )

    synthesis = subparsers.add_parser(
        "synthesis-prompt",
        help="build a source-grounded cross-paper synthesis prompt",
    )
    synthesis.add_argument("--topic", type=Path, required=True)
    synthesis.add_argument("--papers", type=Path, required=True)
    synthesis.add_argument("--analyses", type=Path, required=True)
    synthesis.add_argument(
        "--output",
        type=Path,
        default=Path(".topic-discovery") / "synthesis_prompt.md",
    )

    ideas = subparsers.add_parser(
        "ideas-prompt",
        help="build a diverse research idea generation prompt",
    )
    ideas.add_argument("--topic", type=Path, required=True)
    ideas.add_argument("--synthesis", type=Path, required=True)
    ideas.add_argument("--target-count", type=int, default=6)
    ideas.add_argument(
        "--output",
        type=Path,
        default=Path(".topic-discovery") / "ideas_prompt.md",
    )

    challenge = subparsers.add_parser(
        "challenge",
        help="search nearest prior art and build an adversarial review prompt",
    )
    challenge.add_argument("--idea", type=Path, required=True)
    challenge.add_argument("--year-from", type=int)
    challenge.add_argument("--year-to", type=int)
    challenge.add_argument("--limit", type=int, default=20)
    challenge.add_argument("--top-k", type=int, default=12)
    challenge.add_argument("--max-queries", type=int, default=4)
    _add_provider_argument(challenge)
    challenge.add_argument(
        "--output",
        type=Path,
        default=Path(".topic-discovery") / "prior_art_challenge.json",
    )
    challenge.add_argument(
        "--prompt-output",
        type=Path,
        default=Path(".topic-discovery") / "prior_art_challenge_prompt.md",
    )
    return parser


def _add_provider_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        action="append",
        choices=["all", *[item.value for item in LiteratureProvider]],
        default=None,
    )


def _resolve_input(root: Path, path: Path) -> Path:
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("input files must remain inside --root")
    if not resolved.is_file():
        raise ValueError(f"input file does not exist: {resolved}")
    return resolved


def _resolve_output(root: Path, path: Path) -> Path:
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("output files must remain inside --root")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _load_json(root: Path, path: Path) -> object:
    return json.loads(_resolve_input(root, path).read_text(encoding="utf-8"))


def _selected_providers(
    requested: list[str] | None,
) -> tuple[list[SearchProvider], set[LiteratureProvider] | None]:
    providers = build_default_providers()
    choices = set(requested or ["all"])
    if "all" in choices:
        return providers, None
    selected = {LiteratureProvider(value) for value in choices}
    return (
        [provider for provider in providers if provider.provider in selected],
        selected,
    )


def _search(args: argparse.Namespace, root: Path) -> tuple[dict[str, object], bool]:
    query = LiteratureSearchQuery(
        query=args.query,
        year_from=args.year_from,
        year_to=args.year_to,
        venue=args.venue,
        limit=args.limit,
        offset=args.offset,
    )
    topic_digest = hashlib.sha256(args.query.strip().casefold().encode("utf-8")).hexdigest()[:12]
    topic = TopicSpec(
        id=f"topic-{topic_digest}",
        title=args.topic_title or f"Research topic: {args.query}",
        description=(
            args.topic_description
            or f"Literature landscape and research discovery for: {args.query}."
        ),
        research_area=args.research_area,
        target_venues=[args.venue] if args.venue else [],
        literature_start_year=args.year_from,
        literature_end_year=args.year_to,
    )

    providers, selected = _selected_providers(args.provider)
    report = LiteratureSearchService(providers).search(query, selected_providers=selected)
    landscape = build_research_landscape(topic, report)
    destination = _resolve_output(root, args.output)
    payload = {
        "search_report": report.model_dump(mode="json"),
        "landscape": landscape.model_dump(mode="json"),
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        **search_report_summary(report),
        "topic_id": topic.id,
        "unique_citation_edges": len(landscape.citation_edges),
        "unresolved_reference_count": landscape.unresolved_reference_count,
        "output": str(destination),
        "warning": (
            "Search coverage is provider-dependent. Results and citation counts are not "
            "a proof of novelty; inspect nearest-neighbor papers and counterevidence."
        ),
    }
    return summary, bool(report.pages)


def _synthesis_prompt(args: argparse.Namespace, root: Path) -> tuple[dict[str, object], bool]:
    topic_path = _resolve_input(root, args.topic)
    topic = TopicSpec.model_validate_json(topic_path.read_text(encoding="utf-8"))
    papers_raw = _load_json(root, args.papers)
    analyses_raw = _load_json(root, args.analyses)
    papers = TypeAdapter(list[PaperReference]).validate_python(papers_raw)
    analyses = TypeAdapter(list[PaperAnalysis]).validate_python(analyses_raw)
    prompt = build_research_synthesis_prompt(topic, papers, analyses)
    destination = _resolve_output(root, args.output)
    destination.write_text(prompt, encoding="utf-8")
    return {
        "topic_id": topic.id,
        "paper_count": len(papers),
        "analysis_count": len(analyses),
        "evidence_statement_count": sum(len(item.statements) for item in analyses),
        "output": str(destination),
        "next_step": (
            "Send this prompt to an LLM, then validate its JSON with validate_research_synthesis()."
        ),
    }, True


def _ideas_prompt(args: argparse.Namespace, root: Path) -> tuple[dict[str, object], bool]:
    topic_path = _resolve_input(root, args.topic)
    topic = TopicSpec.model_validate_json(topic_path.read_text(encoding="utf-8"))
    synthesis = ResearchSynthesis.model_validate_json(
        _resolve_input(root, args.synthesis).read_text(encoding="utf-8")
    )
    prompt = build_idea_generation_prompt(topic, synthesis, target_count=args.target_count)
    destination = _resolve_output(root, args.output)
    destination.write_text(prompt, encoding="utf-8")
    return {
        "topic_id": topic.id,
        "synthesis_id": synthesis.id,
        "gap_count": len(synthesis.gaps),
        "target_count": args.target_count,
        "output": str(destination),
        "next_step": (
            "Validate the model response with validate_idea_batch() before prior-art search."
        ),
    }, True


def _challenge(args: argparse.Namespace, root: Path) -> tuple[dict[str, object], bool]:
    idea = IdeaCandidate.model_validate_json(
        _resolve_input(root, args.idea).read_text(encoding="utf-8")
    )
    providers, selected = _selected_providers(args.provider)
    service = PriorArtChallengeService(
        providers,
        max_queries=args.max_queries,
        limit_per_query=args.limit,
        top_k=args.top_k,
    )
    report = service.challenge(
        idea,
        year_from=args.year_from,
        year_to=args.year_to,
        selected_providers=selected,
    )
    prompt = build_prior_art_challenge_prompt(idea, report)
    output_path = _resolve_output(root, args.output)
    prompt_path = _resolve_output(root, args.prompt_output)
    output_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    prompt_path.write_text(prompt, encoding="utf-8")
    papers = {paper.id: paper for paper in report.papers}
    return {
        "challenge_id": report.id,
        "idea_id": idea.id,
        "search_queries": report.search_queries,
        "providers_succeeded": report.providers_succeeded,
        "unique_papers": len(report.papers),
        "nearest_matches": [
            {
                "rank": match.rank,
                "paper_id": match.paper_id,
                "title": papers[match.paper_id].title,
                "score": match.score,
                "matched_terms": match.matched_terms,
            }
            for match in report.nearest_matches
        ],
        "warnings": report.warnings,
        "report_output": str(output_path),
        "prompt_output": str(prompt_path),
        "next_step": (
            "Review the adversarial response with validate_prior_art_review(); "
            "human approval is still required."
        ),
    }, bool(report.providers_succeeded)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "search":
            summary, succeeded = _search(args, root)
        elif args.command == "synthesis-prompt":
            summary, succeeded = _synthesis_prompt(args, root)
        elif args.command == "ideas-prompt":
            summary, succeeded = _ideas_prompt(args, root)
        elif args.command == "challenge":
            summary, succeeded = _challenge(args, root)
        else:
            raise ValueError(f"unknown command: {args.command}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if succeeded else 2
    except (ValueError, RuntimeError, OSError, ValidationError) as exc:
        print(f"epidiscover: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
