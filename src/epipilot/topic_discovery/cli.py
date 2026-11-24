"""Standalone CLI for topic-level literature discovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from epipilot.topic_discovery.landscape import build_research_landscape, search_report_summary
from epipilot.topic_discovery.models import LiteratureProvider, LiteratureSearchQuery, TopicSpec
from epipilot.topic_discovery.providers import build_default_providers
from epipilot.topic_discovery.service import LiteratureSearchService


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
    search.add_argument(
        "--provider",
        action="append",
        choices=["all", *[item.value for item in LiteratureProvider]],
        default=None,
    )
    search.add_argument(
        "--output",
        type=Path,
        default=Path(".topic-discovery") / "landscape.json",
    )
    return parser


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

    providers = build_default_providers()
    requested = set(args.provider or ["all"])
    if "all" not in requested:
        selected = {LiteratureProvider(value) for value in requested}
        providers = [provider for provider in providers if provider.provider in selected]
    else:
        selected = None

    report = LiteratureSearchService(providers).search(
        query,
        selected_providers=selected,
    )
    landscape = build_research_landscape(topic, report)
    destination = (root / args.output).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("discovery output must remain inside --root")
    destination.parent.mkdir(parents=True, exist_ok=True)
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "search":
            summary, succeeded = _search(args, root)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if succeeded else 2
        raise ValueError(f"unknown command: {args.command}")
    except (ValueError, RuntimeError, OSError, ValidationError) as exc:
        print(f"epidiscover: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
