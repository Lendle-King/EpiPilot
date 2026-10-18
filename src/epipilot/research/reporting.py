"""Small JSON/Markdown projections; canonical truth stays in EventStore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from epipilot.research.engine import ResearchEngine


def report(engine: ResearchEngine) -> dict[str, Any]:
    """JSON presentation boundary, including raw metrics and rejection reasons."""
    r = engine.state.research
    if r is None:
        raise ValueError("research not configured")
    ranking = engine.ranking()
    statuses: dict[UUID, str] = {h.id: h.status.value for h in engine.state.hypotheses}
    return {
        "project_id": engine.project_id,
        "event_version": engine.state.event_version,
        "goal": r.goal.model_dump(mode="json"),
        "budget": engine.budget(),
        "running": bool(r.active),
        "rounds_completed": len(r.settlements),
        "stop_reason": ranking.stop_reason,
        "tree": [
            {
                "id": str(n.hypothesis_id),
                "label": n.label,
                "status": statuses[n.hypothesis_id],
                "and_ids": [str(x) for x in n.and_ids],
                "or_ids": [str(x) for x in n.or_ids],
                "basis": n.basis,
                "source_refs": list(n.source_refs),
                "imported_route_state": n.imported_route_state,
            }
            for n in r.nodes
        ],
        "observations": [o.model_dump(mode="json") for o in r.observations],
        "reviews": [x.model_dump(mode="json") for x in r.reviews],
        "ranking": ranking.model_dump(mode="json"),
    }


def write_report(path: Path, data: dict[str, Any], title: str = "CARTS research report") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    lines = [f"# {title}", "", f"Project: `{data.get('project_id', 'benchmark')}`", ""]
    if "budget" in data:
        lines += [f"Budget: `{json.dumps(data['budget'])}`", ""]
    for node in data.get("tree", []):
        lines.append(f"- **{node['status']}** — {node['label']}")
    if "observations" in data:
        lines += ["", "## Executed observations", ""]
        reviews = {r["assessment"]["proposal_id"]: r["assessment"] for r in data.get("reviews", [])}
        for o in data["observations"]:
            assessment = reviews.get(o["proposal_id"], {})
            metrics = ", ".join(f"{m['name']}={m['value']:.6g}" for m in o["metrics"])
            lines += [
                f"- {o['proposal_id']}: execution={o['execution']}, "
                f"science={assessment.get('outcome', 'unverified')}; {metrics}"
            ]
    if "decisions" in data:
        lines += [
            "",
            "## Chronological replay",
            "",
            "Historical costs are unknown; relative-cost rankings are sensitivity assumptions.",
            "",
        ]
        for decision in data["decisions"]:
            lines += [f"- {decision['at']}: {decision['new_information']}"]
    if "ranking" in data:
        lines += ["", "## Next candidates", ""]
        for item in data["ranking"]["ranked"]:
            lines.append(f"- {item['proposal_id']}: {item['score']:.4f}; {item['explanation']}")
        for item in data["ranking"]["rejected"]:
            lines.append(f"- Excluded {item['proposal_id']}: {item['reason']}")
        lines += ["", f"Stop: {data['ranking']['stop_reason']}"]
    if "summary" in data and isinstance(data["summary"], list):
        lines += [
            "",
            "| Scenario | Policy | Best utility | 95% CI | Target rate | Regret |",
            "|---|---|---:|---|---:|---:|",
        ]
        for row in data["summary"]:
            lines.append(
                f"| {row['scenario']} | {row['strategy']} | {row['best_utility_mean']:.3f} | "
                f"[{row['best_utility_ci95_low']:.3f}, {row['best_utility_ci95_high']:.3f}] | "
                f"{row['target_reached_mean']:.2f} | {row['regret_mean']:.3f} |"
            )
    lines += [
        "",
        "Full machine-readable provenance, observations and limitations are in the adjacent JSON.",
    ]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf8")
