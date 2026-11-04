"""Source-pinned, offline adapter qualification for external research agents.

No training, model invocations or third-party module imports are performed.
The source pin is not itself a license review, adapter test or sandbox audit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from epipilot.benchfirst.contracts import BenchmarkPlan

CATALOG: tuple[tuple[str, str, str, str], ...] = (
    (
        "agent_only",
        "https://github.com/Lendle-King/EpiPilot",
        "Apache-2.0",
        "Coding-agent-only control: no EpiPilot research policy enabled",
    ),
    (
        "aide_ml",
        "https://github.com/WecoAI/aideml",
        "MIT",
        "Code-tree search; highest-priority search-based candidate",
    ),
    (
        "aira_dojo",
        "https://github.com/facebookresearch/aira-dojo",
        "CC BY-NC 4.0; check noncommercial restrictions",
        "Multiple search policies; setup may require Docker/Slurm integration",
    ),
    (
        "autoresearch",
        "https://github.com/karpathy/autoresearch",
        "Check upstream license and hardware requirements",
        "Simple fixed-budget research-loop control; GPU workload not portable by default",
    ),
    (
        "epipilot_current",
        "https://github.com/Lendle-King/EpiPilot",
        "Apache-2.0",
        "Existing EpiPilot heuristic and evidence memory",
    ),
    (
        "evocast",
        "https://github.com/18e0-x/EvoCast",
        "Check upstream license and GPU/time-series constraints",
        "Domain-specific evidence-grounded architecture evolution",
    ),
    (
        "youra",
        "https://github.com/PrayPrey/Your-Research-Agent",
        "Check upstream license and Claude Code dependencies",
        "Scientific provenance, failure recovery and persistent state",
    ),
    (
        "ai_scientist_v2",
        "https://github.com/SakanaAI/AI-Scientist-v2",
        "Check upstream code license and executable-code isolation",
        "Full research lifecycle control, not primarily metric optimization",
    ),
)


def catalog() -> list[dict[str, str]]:
    return [
        {"id": name, "source_url": url, "license_note": license_note, "role": role}
        for name, url, license_note, role in CATALOG
    ]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        raise ValueError("git checkout inspection failed")
    return result.stdout.strip()


def source_preflight(plan: BenchmarkPlan, workspace: Path) -> dict[str, Any]:
    """Inspect exact local Git sources; never clone or execute upstream code."""
    root = workspace.resolve()
    if not root.is_dir():
        raise ValueError("adapter workspace does not exist")
    items: list[dict[str, object]] = []
    for adapter in plan.adapters:
        candidate = root / adapter.checkout
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or not candidate.is_dir():
            items.append(
                {
                    "id": adapter.id,
                    "status": "missing_or_unsafe_checkout",
                    "expected": adapter.source_sha,
                }
            )
            continue
        try:
            head = _git(resolved, "rev-parse", "HEAD")
            dirty = bool(_git(resolved, "status", "--porcelain", "--untracked-files=normal"))
            git_present = _git(resolved, "rev-parse", "--show-toplevel")
            if Path(git_present).resolve() != resolved:
                raise ValueError("checkout root must be a standalone Git worktree")
        except (ValueError, OSError, subprocess.TimeoutExpired):
            items.append({"id": adapter.id, "status": "invalid_git_checkout"})
            continue
        if head != adapter.source_sha or dirty:
            status = "revision_or_cleanliness_mismatch"
        elif adapter.adapter_qualified:
            status = "source_pinned_adapter_claimed_ready"
        else:
            status = "source_pinned_adapter_not_qualified"
        items.append(
            {
                "id": adapter.id,
                "status": status,
                "expected": adapter.source_sha,
                "observed": head,
                "dirty": dirty,
            }
        )
    all_pinned = all(
        item["status"]
        in ("source_pinned_adapter_claimed_ready", "source_pinned_adapter_not_qualified")
        for item in items
    )
    all_qualified = all(item["status"] == "source_pinned_adapter_claimed_ready" for item in items)
    return {
        "name": plan.name,
        "status": "source_preflight_complete" if all_pinned else "source_preflight_blocked",
        "source_pins_match": all_pinned,
        "adapter_self_reported_qualification": all_qualified,
        "adapters": items,
        "model_calls": 0,
        "qualification": (
            "Git SHA and worktree cleanliness checked locally; adapters were NOT run. "
            "The adapter_qualified bit is self-reported, not independent evidence of "
            "model compatibility, evaluator isolation, license compliance or metric fidelity."
        ),
    }
