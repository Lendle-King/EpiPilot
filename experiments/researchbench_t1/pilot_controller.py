"""Sequential four-policy T1 pilot controller with bounded Codex decisions."""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from epipilot.research.cli import _next_action
from epipilot.research.decision_models import (
    DecisionBranch,
    DecisionEstimate,
    DecisionOption,
    OutcomeKind,
)
from epipilot.research.decision_value import rank_decision
from epipilot.research.models import (
    EvidenceRecord,
    Experiment,
    Proposal,
    ProposalKind,
    ResearchGoal,
    ResearchState,
    Unknown,
)
from epipilot.research.planner import rank_proposals
from experiments.researchbench_t1.resource_guard import ResourceRefusal
from experiments.researchbench_t1.t1 import (
    EXECUTION_ORDER_SEED,
    STAGES,
    BenchmarkError,
    _code_revision,
    add_memory,
    atomic_json,
    canonical,
    digest,
    event,
    exclusive,
    file_digest,
    ledger,
    normalize_candidate,
    read_json,
    record_agent_call_finished,
    record_agent_call_started,
    record_cell_incident,
    record_cell_terminal,
    select,
    status,
)

CandidateSchema: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "model": {"const": "logistic"},
                "feature_mode": {"enum": ["all", "numeric_only"]},
                "C": {"type": "number", "minimum": 0.01, "maximum": 10},
                "class_weight": {"enum": ["none", "balanced"]},
            },
            "required": ["model", "feature_mode", "C", "class_weight"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "model": {"const": "forest"},
                "feature_mode": {"enum": ["all", "numeric_only"]},
                "n_estimators": {"type": "integer", "minimum": 20, "maximum": 120},
                "max_depth": {"type": "integer", "minimum": 2, "maximum": 16},
                "min_samples_leaf": {"type": "integer", "minimum": 1, "maximum": 10},
                "class_weight": {"enum": ["none", "balanced"]},
            },
            "required": [
                "model",
                "feature_mode",
                "n_estimators",
                "max_depth",
                "min_samples_leaf",
                "class_weight",
            ],
            "additionalProperties": False,
        },
    ]
}


class ModelCallFailure(BenchmarkError):
    def __init__(self, classification: str, *, infrastructure: bool):
        super().__init__(f"model call failed: {classification}")
        self.classification = classification
        self.infrastructure = infrastructure


def _redact_diagnostic(raw: str, *, call_dir: Path | None = None, max_chars: int = 1200) -> str:
    """Keep a short CLI diagnostic while removing credential-like strings."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
    if call_dir is not None:
        text = text.replace(str(call_dir), "<call-dir>")
    text = text.replace(str(Path.home()), "~")
    patterns = (
        (r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", "[REDACTED AUTH]"),
        (
            r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|x-api-key)(\s*[:=]\s*)[^\s,;]+",
            r"\1\2[REDACTED]",
        ),
        (r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED KEY]"),
        (r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", "[REDACTED TOKEN]"),
        (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[REDACTED TOKEN]"),
        (r"https?://[^\s/@:]+:[^\s/@]+@", "https://[REDACTED]@"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    secret_names = re.compile(r"(?i)(TOKEN|SECRET|API.?KEY|PASSWORD|CREDENTIAL|AUTH)")
    for name, value in sorted(os.environ.items(), key=lambda pair: len(pair[1]), reverse=True):
        if secret_names.search(name) and len(value) >= 6:
            text = text.replace(value, "[REDACTED ENV SECRET]")
    normalized = "\n".join(text.splitlines())
    return normalized[-max_chars:]


def _stdout_event_types(raw: str) -> list[str]:
    result: list[str] = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        kind = row.get("type") if isinstance(row, dict) else None
        if isinstance(kind, str) and kind not in result:
            result.append(kind[:80])
        if len(result) == 12:
            break
    return result


def _structured_cli_errors(raw: str, *, call_dir: Path | None = None) -> list[dict[str, str]]:
    """Extract only bounded, redacted error fields from CLI JSONL events."""
    result: list[dict[str, str]] = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") not in {"error", "turn.failed"}:
            continue
        nested = record.get("error")
        error = nested if isinstance(nested, dict) else {}
        row: dict[str, str] = {"event_type": str(record["type"])[:80]}
        if isinstance(nested, str):
            message_value: Any = nested
        else:
            message_value = error.get("message", record.get("message"))
        error_type = error.get("type", record.get("error_type"))
        code = error.get("code", record.get("code"))
        param = error.get("param", record.get("param"))
        for key, value in (
            ("error_type", error_type),
            ("code", code),
            ("param", param),
            ("message", message_value),
        ):
            if isinstance(value, str) and value:
                row[key] = _redact_diagnostic(value, call_dir=call_dir, max_chars=1200)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                row[key] = str(value)
        result.append(row)
        if len(result) == 3:
            break
    return result


def _replace_json_file(path: Path, data: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=".recovery-meta-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical(data))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _schema(arm: str, purpose: str) -> dict[str, Any]:
    if purpose == "memory_note":
        return {
            "type": "object",
            "properties": {"note": {"type": "string", "minLength": 20, "maxLength": 500}},
            "required": ["note"],
            "additionalProperties": False,
        }
    if arm in {"agent_only", "memory_only"}:
        return {
            "type": "object",
            "properties": {"candidate": CandidateSchema},
            "required": ["candidate"],
            "additionalProperties": False,
        }
    if arm == "epipilot_heuristic":
        proposal = {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "minLength": 8, "maxLength": 240},
                "candidate": CandidateSchema,
                "kind": {"enum": ["explore", "optimize", "verify"]},
                "information_gain": {"type": "number", "minimum": 0, "maximum": 1},
                "expected_improvement": {"type": "number", "minimum": 0, "maximum": 1},
                "unlock_value": {"type": "number", "minimum": 0, "maximum": 1},
                "novelty": {"type": "number", "minimum": 0, "maximum": 1},
                "risk": {"type": "number", "minimum": 0, "maximum": 1},
                "followup_hypothesis": {"type": "string", "minLength": 8, "maxLength": 240},
                "followup_candidate": CandidateSchema,
                "followup_kind": {"enum": ["explore", "optimize", "verify"]},
                "followup_information_gain": {"type": "number", "minimum": 0, "maximum": 1},
                "followup_expected_improvement": {"type": "number", "minimum": 0, "maximum": 1},
                "followup_unlock_value": {"type": "number", "minimum": 0, "maximum": 1},
                "followup_novelty": {"type": "number", "minimum": 0, "maximum": 1},
                "followup_risk": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "hypothesis",
                "candidate",
                "kind",
                "information_gain",
                "expected_improvement",
                "unlock_value",
                "novelty",
                "risk",
                "followup_hypothesis",
                "followup_candidate",
                "followup_kind",
                "followup_information_gain",
                "followup_expected_improvement",
                "followup_unlock_value",
                "followup_novelty",
                "followup_risk",
            ],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "proposals": {"type": "array", "minItems": 2, "maxItems": 2, "items": proposal}
            },
            "required": ["proposals"],
            "additionalProperties": False,
        }
    if arm == "epipilot_conditional":
        decision = {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "minLength": 8, "maxLength": 240},
                "candidate": CandidateSchema,
                "risk": {"type": "number", "minimum": 0, "maximum": 1},
                "probabilities": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "branch_utilities": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "stop": {"type": "number", "minimum": 0, "maximum": 1},
                            "alternative": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["stop", "alternative"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["hypothesis", "candidate", "risk", "probabilities", "branch_utilities"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "decisions": {"type": "array", "minItems": 2, "maxItems": 2, "items": decision}
            },
            "required": ["decisions"],
            "additionalProperties": False,
        }
    raise ValueError("unknown Pilot model-call schema")


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_model_json(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > 20_000:
        raise ValueError("model reply exceeded the frozen output cap")
    decoded = json.loads(raw, object_pairs_hook=_strict_object_pairs)
    if not isinstance(decoded, dict):
        raise ValueError("model reply must be one JSON object")
    return decoded


def _build_research_state(
    proposals: list[Proposal], prior_trials: list[dict[str, Any]]
) -> ResearchState:
    state = ResearchState(
        goal=ResearchGoal(
            statement=(
                "Maximize WDBC development AUROC under two 15-second trials, with a reserved "
                "20-second final-evaluator ceiling."
            ),
            success_metrics=["development AUROC"],
            constraints=[
                "Only the isolated train/dev split is available; final evaluation time is reserved "
                "and unavailable to research decisions."
            ],
            budget=50.0,
        ),
        fixed_reserve=20.0,
        unknowns={
            "u-validation-response": Unknown(
                id="u-validation-response",
                question="Which candidate decision is most useful on the current dev split?",
                importance=0.7,
                decision_sensitivity=0.7,
            )
        },
    )
    for old in prior_trials:
        proposal_record = old.get("planner_proposal")
        if not isinstance(proposal_record, dict):
            continue
        proposal = Proposal.model_validate({**proposal_record, "status": "completed"})
        state.proposals[proposal.id] = proposal
        experiment_id = f"exp-{old['run_id']}"
        evidence_id = f"evidence-{old['run_id']}"
        evidence = EvidenceRecord(
            id=evidence_id,
            experiment_id=experiment_id,
            summary=(
                f"T1 worker measured development AUROC {old['dev_auroc']:.6f}; "
                "descriptive split-specific result."
            ),
            artifact_refs=[f"runs/{old['run_id']}/metric.json"],
            independently_verified=True,
            measurement_validated=True,
            validated_metrics={"development_auroc": float(old["dev_auroc"])},
            artifact_sha256=old.get("metric_sha256"),
        )
        state.evidence[evidence_id] = evidence
        state.experiments[experiment_id] = Experiment(
            id=experiment_id,
            proposal_id=proposal.id,
            summary=f"Observed development AUROC {old['dev_auroc']:.6f} on the frozen T1 split.",
            outcome="inconclusive",
            actual_cost=float(old["seconds"]),
            git_revision="0" * 40,
            config_ref=f"sha256:{old['candidate_sha256']}",
            dataset_ref="ResearchBench-T1/WDBC/dev",
            seeds=[int(old["seed"])],
            metrics={"development_auroc": float(old["dev_auroc"])},
            evidence_id=evidence_id,
        )
    for proposal in proposals:
        state.proposals[proposal.id] = proposal
    return state


def _proposal(
    raw: dict[str, Any], proposal_id: str, *, parent: str | None = None, followup: bool = False
) -> tuple[Proposal, dict[str, Any]]:
    prefix = "followup_" if followup else ""
    candidate = normalize_candidate(raw[f"{prefix}candidate"])
    kind = ProposalKind(raw[f"{prefix}kind"])
    proposal = Proposal(
        id=proposal_id,
        title=f"T1 {kind.value} candidate {proposal_id}",
        kind=kind,
        rationale=raw[f"{prefix}hypothesis"],
        acceptance="Controller executed this candidate within the preregistered worker limit.",
        unknown_ids=["u-validation-response"],
        depends_on=[parent] if parent else [],
        information_gain=float(raw[f"{prefix}information_gain"]),
        expected_improvement=float(raw[f"{prefix}expected_improvement"]),
        unlock_value=float(raw[f"{prefix}unlock_value"]),
        novelty=float(raw[f"{prefix}novelty"]),
        estimated_cost=15.0,
        risk=float(raw[f"{prefix}risk"]),
    )
    return proposal, candidate


def choose_heuristic(
    payload: dict[str, Any], prior_trials: list[dict[str, Any]], call_no: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    if set(payload) != {"proposals"} or len(payload["proposals"]) != 2:
        raise ValueError("heuristic decision needs exactly two proposal paths")
    proposals: list[Proposal] = []
    candidates: dict[str, dict[str, Any]] = {}
    for i, raw in enumerate(payload["proposals"], 1):
        proposal_id = f"call{call_no}_p{i}"
        root, candidate = _proposal(raw, proposal_id)
        child, _child_candidate = _proposal(
            raw, f"call{call_no}_p{i}_next", parent=proposal_id, followup=True
        )
        proposals.extend((root, child))
        candidates[proposal_id] = candidate
    state = _build_research_state(proposals, prior_trials)
    ranked = rank_proposals(state, limit=2)
    if not ranked:
        raise ValueError("EpiPilot rank_proposals found no feasible candidate")
    selected = ranked[0].proposal
    decision = {
        "selection_policy": "epipilot.research.planner.rank_proposals",
        "phase": ranked[0].phase,
        "selected_proposal_id": selected.id,
        "ranked_proposals": [
            {
                "proposal_id": row.proposal.id,
                "score": row.score,
                "phase": row.phase,
                "rationale": row.rationale,
            }
            for row in ranked
        ],
        "lookahead": (
            "two-step feasible immediate-successor bonus; heuristic, not calibrated planning"
        ),
    }
    return candidates[selected.id], {
        **decision,
        "planner_proposal": selected.model_dump(mode="json"),
    }


def choose_conditional(
    payload: dict[str, Any], prior_trials: list[dict[str, Any]], call_no: int
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if set(payload) != {"decisions"} or len(payload["decisions"]) != 2:
        raise ValueError("conditional decision needs exactly two candidate estimates")
    proposals: list[Proposal] = []
    decisions = payload["decisions"]
    candidate_by_id: dict[str, dict[str, Any]] = {}
    for i, raw in enumerate(decisions, 1):
        candidate = normalize_candidate(raw["candidate"])
        proposal_id = f"call{call_no}_c{i}"
        proposal = Proposal(
            id=proposal_id,
            title=f"T1 conditional candidate {proposal_id}",
            kind=ProposalKind.OPTIMIZE,
            rationale=raw["hypothesis"],
            acceptance="Controller executed this candidate within the preregistered worker limit.",
            unknown_ids=["u-validation-response"],
            information_gain=0.5,
            expected_improvement=0.5,
            estimated_cost=15.0,
            risk=float(raw["risk"]),
        )
        proposals.append(proposal)
        candidate_by_id[proposal_id] = candidate
    state = _build_research_state(proposals, prior_trials)
    for index, raw in enumerate(decisions):
        proposal_id = f"call{call_no}_c{index + 1}"
        alt_id = f"call{call_no}_c{2 if index == 0 else 1}"
        probabilities = tuple(float(value) for value in raw["probabilities"])
        if (
            any(
                type(value) is not float and type(value) is not int
                for value in raw["probabilities"]
            )
            or abs(sum(probabilities) - 1.0) > 1e-8
        ):
            raise ValueError("conditional outcome probabilities must sum to one")
        if len(raw["branch_utilities"]) != 4:
            raise ValueError("conditional decision table must contain four outcomes")
        options = (
            DecisionOption(
                key="stop",
                prior_utility=sum(
                    p * float(branch["stop"])
                    for p, branch in zip(probabilities, raw["branch_utilities"], strict=True)
                ),
            ),
            DecisionOption(
                key="alternative-after-result",
                proposal_id=alt_id,
                prior_utility=sum(
                    p * float(branch["alternative"])
                    for p, branch in zip(probabilities, raw["branch_utilities"], strict=True)
                ),
            ),
        )
        branches = tuple(
            DecisionBranch(
                outcome=kind,
                probability=probabilities[i],
                utilities=(
                    float(raw["branch_utilities"][i]["stop"]),
                    float(raw["branch_utilities"][i]["alternative"]),
                ),
            )
            for i, kind in enumerate(OutcomeKind)
        )
        estimate = DecisionEstimate(
            proposal_id=proposal_id,
            predictor="agent-supplied-uncalibrated-heuristic",
            utility_version="researchbench-t1-pilot-v1",
            group="wdbc",
            rationale=raw["hypothesis"],
            options=options,
            branches=branches,
        )
        state.decision_estimates[estimate.proposal_id] = estimate
    action = _next_action(state, policy="conditional")
    scored = [
        rank_decision(state, estimate).model_dump(mode="json")
        for estimate in state.decision_estimates.values()
    ]
    if action["status"] == "decision_value_stop":
        return None, {
            "selection_policy": "epipilot.research.cli._next_action(policy='conditional')",
            "action": action,
            "decision_scores": scored,
            "forecast_qualification": (
                "agent-supplied, uncalibrated four-outcome heuristic; not observed causal value"
            ),
            "stopped_without_fallback": True,
        }
    if action["status"] != "ready_for_agent":
        raise ValueError(f"conditional policy did not produce a decision: {action['status']}")
    selected_id = str(action["decision_score"]["proposal_id"])
    return candidate_by_id[selected_id], {
        "selection_policy": "epipilot.research.cli._next_action(policy='conditional')",
        "action": action,
        "decision_scores": scored,
        "forecast_qualification": (
            "agent-supplied, uncalibrated four-outcome heuristic; not observed causal value"
        ),
        "stopped_without_fallback": False,
        "planner_proposal": state.proposals[selected_id].model_dump(mode="json"),
    }


def _call_prompt(
    arm: str, purpose: str, dataset: str, seed: int, history: list[dict[str, Any]], notes: list[str]
) -> str:
    context = {
        "task": (
            "Select bounded hyperparameters to improve development AUROC on "
            "Wisconsin diagnostic breast cancer."
        ),
        "dataset": dataset,
        "seed": seed,
        "candidate_space": {
            "models": "regularized logistic regression or random forest",
            "feature_mode": ["all", "numeric_only"],
            "class_weight": ["none", "balanced"],
            "metric": "development AUROC",
            "trials_remaining": max(0, 2 - len(history)),
        },
        "development_history": [
            {"run_id": row["run_id"], "dev_auroc": row["dev_auroc"], "candidate": row["candidate"]}
            for row in (history if arm != "memory_only" or purpose == "memory_note" else [])
        ],
        "memory_notes": notes if arm == "memory_only" else [],
        "strategy": arm,
    }
    text = (
        "Make one controlled T1 research decision. You cannot run commands, inspect files, "
        "browse, or access the holdout. "
        "Use only development measurements. State no result as established; any rationale is a "
        "hypothesis. Return one JSON object conforming to the output schema, without retries, "
        "tools, explanations, or markdown.\n"
    )
    if purpose == "memory_note":
        text += (
            "Return this grounded note format using the supplied values: `On frozen split, run "
            "<run_id>, candidate sha256 <hash>, development AUROC "
            "<score-to-six-decimals>; scope=current cell only.` Add no other claim or scope.\n"
        )
        context = {
            "strategy": arm,
            "dataset": dataset,
            "seed": seed,
            "verified_trial": (
                {
                    "run_id": history[-1]["run_id"],
                    "candidate_sha256": history[-1]["candidate_sha256"],
                    "dev_auroc": history[-1]["dev_auroc"],
                    "candidate": history[-1]["candidate"],
                }
                if history
                else None
            ),
        }
    elif arm == "agent_only":
        text += "Use ordinary trial feedback only. No planner and no saved experiment memory.\n"
    elif arm == "memory_only":
        text += (
            "Use trial feedback and evidence-linked memory notes only; do not use "
            "an EpiPilot planner. Memory is isolated to this seed cell.\n"
        )
    elif arm == "epipilot_heuristic":
        text += (
            "For exactly two paths, provide a root proposal and a feasible follow-up proposal "
            "with bounded heuristic scores. The controller passes them to EpiPilot rank_proposals, "
            "which applies its two-step lookahead. Scores are uncalibrated inputs, not facts.\n"
        )
    elif arm == "epipilot_conditional":
        text += (
            "For two candidate experiments, provide probability forecasts in canonical outcome "
            "order [improved, no_gain, inconclusive, infrastructure_failure], summing to one, and "
            "utilities for [stop, alternative] under each outcome. The controller uses EpiPilot's "
            "existing net information-value function. Forecasts are uncalibrated heuristics. A "
            "nonpositive net value must stop without fallback.\n"
        )
    return (
        text
        + "Cell information (no train/dev rows or labels):\n"
        + json.dumps(context, sort_keys=True, separators=(",", ":"))
    )


def _usage_from_jsonl(raw: str) -> tuple[int, int, int, int, int, bool]:
    turns = 0
    tools = 0
    input_tokens = output_tokens = cached_tokens = 0
    usage_available = 0
    for line in raw.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") == "turn.completed":
            turns += 1
            usage = record.get("usage") or record.get("total_token_usage") or {}
            if isinstance(usage, dict):
                details = usage.get("input_tokens_details") or {}
                if "input_tokens" in usage or "output_tokens" in usage:
                    usage_available += 1
                    input_tokens += int(usage.get("input_tokens", 0) or 0)
                    output_tokens += int(usage.get("output_tokens", 0) or 0)
                    cached_tokens += int(
                        details.get("cached_tokens", usage.get("cached_input_tokens", 0)) or 0
                    )
        item = record.get("item")
        item_type = item.get("type", "") if isinstance(item, dict) else ""
        if "tool" in str(item_type).lower() or item_type in {
            "command_execution",
            "shell_command",
            "web_search",
            "mcp_tool_call",
        }:
            tools += 1
    return (
        turns,
        tools,
        input_tokens,
        output_tokens,
        cached_tokens if usage_available else 0,
        bool(usage_available),
    )


def model_call(
    *,
    work: Path,
    codex: str,
    arm: str,
    dataset: str,
    seed: int,
    call_id: str,
    call_no: int,
    purpose: str,
    history: list[dict[str, Any]],
    notes: list[str],
    guarded_call: Callable[..., Any],
    max_timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record_agent_call_started(
        work,
        arm,
        dataset,
        seed,
        call_id,
        purpose,
        model="gpt-6-luna",
        reasoning_effort="max",
    )
    call_dir = Path(tempfile.mkdtemp(prefix="t1-call-", dir=work.parent))
    schema_path, response_path = call_dir / "schema.json", call_dir / "response.json"
    started_marker = call_dir / "process_attempt.started"
    schema = _schema(arm, purpose)
    schema_path.write_text(json.dumps(schema, separators=(",", ":")), encoding="utf-8")
    prompt = _call_prompt(arm, purpose, dataset, seed, history, notes)
    argv = [
        codex,
        "--no-daemon",
        "exec",
        "--model",
        "gpt-6-luna",
        "-c",
        'model_reasoning_effort="max"',
        "-c",
        "features.shell_tool=false",
        "-c",
        'web_search="disabled"',
        "-c",
        "mcp_servers={}",
        "-c",
        "model_providers.cc-switch-official.request_max_retries=0",
        "-c",
        "model_providers.cc-switch-official.stream_max_retries=0",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(response_path),
        "-C",
        str(call_dir),
        prompt,
    ]
    begin = time.monotonic()
    outcome = "failed"
    telemetry: dict[str, Any] = {
        "usage_available": False,
        "retry_controls": "request_max_retries=0,stream_max_retries=0",
    }
    result: Any | None = None
    try:
        result = guarded_call(
            argv,
            cwd=call_dir,
            env=_agent_environment(),
            timeout=max_timeout,
            started_marker=started_marker,
        )
        elapsed = time.monotonic() - begin
        stdout = result.stdout
        turns, tool_events, input_tokens, output_tokens, cached_tokens, usage_available = (
            _usage_from_jsonl(stdout)
        )
        error_events = _structured_cli_errors(stdout, call_dir=call_dir)
        telemetry.update(
            {
                "elapsed_seconds": round(elapsed, 4),
                "turn_count": turns,
                "tool_event_count": tool_events,
                "usage_available": usage_available,
                "process_started": started_marker.exists(),
                "exit_code": int(result.returncode),
                "stdout_event_types": _stdout_event_types(stdout),
                "stdout_error_events": error_events,
            }
        )
        if usage_available:
            telemetry.update(
                {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_input_tokens": cached_tokens,
                }
            )
        if tool_events:
            outcome = "tool_use_rejected"
            telemetry["failure_classification"] = "tool_event_rejected"
            telemetry["stderr_excerpt_redacted"] = _redact_diagnostic(
                result.stderr, call_dir=call_dir
            )
            raise ModelCallFailure("tool_event_rejected", infrastructure=True)
        if result.returncode != 0:
            outcome = "failed"
            telemetry["failure_classification"] = "cli_nonzero_exit"
            telemetry["stderr_excerpt_redacted"] = _redact_diagnostic(
                result.stderr, call_dir=call_dir
            )
            raise ModelCallFailure("cli_nonzero_exit", infrastructure=True)
        if turns != 1:
            outcome = "failed"
            telemetry["failure_classification"] = "unexpected_turn_count"
            telemetry["stderr_excerpt_redacted"] = _redact_diagnostic(
                result.stderr, call_dir=call_dir
            )
            raise ModelCallFailure("unexpected_turn_count", infrastructure=True)
        if not response_path.is_file():
            outcome = "failed"
            telemetry["failure_classification"] = "missing_structured_output"
            telemetry["stderr_excerpt_redacted"] = _redact_diagnostic(
                result.stderr, call_dir=call_dir
            )
            raise ModelCallFailure("missing_structured_output", infrastructure=True)
        raw = response_path.read_text(encoding="utf-8")
        payload = parse_model_json(raw)
        outcome = "success"
        record_agent_call_finished(work, call_id, outcome, telemetry)
        return payload, {
            "turn_count": turns,
            "tool_event_count": tool_events,
            "usage_available": telemetry["usage_available"],
            **{
                k: telemetry[k]
                for k in ("input_tokens", "output_tokens", "cached_input_tokens")
                if k in telemetry
            },
        }
    except ResourceRefusal as exc:
        outcome = "timeout" if "timeout" in str(exc).lower() else "failed"
        telemetry["elapsed_seconds"] = round(time.monotonic() - begin, 4)
        telemetry.setdefault("turn_count", 0)
        telemetry.setdefault("tool_event_count", 0)
        telemetry["process_started"] = started_marker.exists()
        telemetry["exit_code"] = None
        telemetry["failure_classification"] = (
            "guard_timeout" if outcome == "timeout" else "resource_guard_refusal"
        )
        telemetry["stderr_excerpt_redacted"] = _redact_diagnostic(str(exc), call_dir=call_dir)
        telemetry["stdout_event_types"] = []
        telemetry["stdout_error_events"] = []
        record_agent_call_finished(work, call_id, outcome, telemetry)
        raise
    except ModelCallFailure:
        telemetry.setdefault("failure_classification", "model_call_failed")
        telemetry.setdefault("stderr_excerpt_redacted", "")
        telemetry.setdefault("exit_code", None if result is None else int(result.returncode))
        telemetry.setdefault("stdout_event_types", [])
        telemetry.setdefault("stdout_error_events", [])
        record_agent_call_finished(work, call_id, outcome, telemetry)
        raise
    except Exception as exc:
        if outcome != "tool_use_rejected":
            outcome = "invalid_output" if response_path.exists() else outcome
        telemetry.setdefault("elapsed_seconds", round(time.monotonic() - begin, 4))
        telemetry.setdefault("turn_count", 0)
        telemetry.setdefault("tool_event_count", 0)
        telemetry["process_started"] = started_marker.exists()
        telemetry["exit_code"] = None if result is None else int(result.returncode)
        telemetry["failure_classification"] = (
            "invalid_structured_output"
            if response_path.exists()
            else "local_cli_setup_error"
            if result is None
            else "cli_nonzero_exit"
        )
        telemetry["stderr_excerpt_redacted"] = _redact_diagnostic(
            "" if result is None else result.stderr, call_dir=call_dir
        )
        telemetry["stdout_event_types"] = _stdout_event_types(
            "" if result is None else result.stdout
        )
        telemetry["stdout_error_events"] = _structured_cli_errors(
            "" if result is None else result.stdout, call_dir=call_dir
        )
        record_agent_call_finished(work, call_id, outcome, telemetry)
        classification = telemetry["failure_classification"]
        raise ModelCallFailure(
            classification, infrastructure=classification != "invalid_structured_output"
        ) from exc
    finally:
        shutil.rmtree(call_dir, ignore_errors=True)


def _agent_environment() -> dict[str, str]:
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GITHUB_", "ACTIONS_", "RUNNER_"))
        and key not in {"GH_TOKEN", "GIT_ASKPASS", "SSH_AUTH_SOCK", "ACTIONS_RUNTIME_TOKEN"}
    }
    result["GIT_TERMINAL_PROMPT"] = "0"
    result["CI"] = "1"
    return result


def _record_strategy_decision(
    work: Path, arm: str, dataset: str, seed: int, call_no: int, decision: dict[str, Any]
) -> None:
    with exclusive(work):
        event(
            work,
            {
                "kind": "strategy_decision",
                "arm": arm,
                "dataset": dataset,
                "seed": seed,
                "decision_call": call_no,
                "decision": decision,
            },
        )


def _reconcile_open_cell_trials(
    *,
    work: Path,
    arm: str,
    dataset: str,
    seed: int,
    t1_call: Callable[..., dict[str, Any]],
    inspect: Callable[[], Any],
) -> bool:
    history = ledger(work)
    starts = [
        row
        for row in history
        if row["kind"] == "started"
        and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
    ]
    ends = {row["id"] for row in history if row["kind"] in {"finished", "aborted"}}
    open_ids = [row["id"] for row in starts if row["id"] not in ends]
    if not open_ids:
        return True
    try:
        inspect()
        for run_id in open_ids:
            t1_call(
                "abort-interrupted",
                "--work",
                str(work),
                "--id",
                run_id,
                "--reason",
                "Guarded T1 subprocess interrupted; charge full trial timeout and do not retry.",
                timeout=20,
            )
    except (BenchmarkError, ResourceRefusal):
        return False
    return True


def _load_frozen_pilot(root: Path) -> tuple[dict[str, Any], Path, Path]:
    work, vault = root / "pilot_work", root / "pilot_vault"
    try:
        meta = read_json(work / "meta.json")
        vault_meta = read_json(vault / "meta.json")
    except (OSError, ValueError) as exc:
        raise BenchmarkError(
            "prepare and freeze the Pilot manifest before launching model calls"
        ) from exc
    if meta.get("stage") != "pilot" or meta.get("profile") != STAGES["pilot"]:
        raise BenchmarkError("prepared manifest does not match the frozen Pilot profile")
    if meta.get("code_revision") != _code_revision():
        raise BenchmarkError("Pilot code revision changed after the manifest was prepared")
    if meta.get("execution_order_seed") != EXECUTION_ORDER_SEED:
        raise BenchmarkError("Pilot execution-order seed differs from the frozen protocol")
    expected = [
        (arm, dataset, seed)
        for dataset in meta["profile"]["datasets"]
        for seed in meta["profile"]["seeds"]
        for arm in meta["profile"]["arms"]
    ]
    random.Random(EXECUTION_ORDER_SEED).shuffle(expected)
    if meta.get("execution_order") != [list(cell) for cell in expected]:
        raise BenchmarkError("Pilot execution order differs from the deterministic frozen order")
    events = ledger(work)
    recovery = meta.get("recovery")
    if (
        meta.get("cell_count") != 8
        or meta.get("per_cell_trial_budget_seconds") != 30
        or meta.get("required_development_seconds_for_all_cells") != 240
        or meta.get("target_auroc") != 0.95
        or meta.get("final_holdout_is_off_limits_during_search") is not True
        or vault_meta.get("public_meta_sha256") != file_digest(work / "meta.json")
        or (bool(events) and not isinstance(recovery, dict))
        or list((work / "submissions").glob("*.json"))
        or (work / "final.started").exists()
    ):
        raise BenchmarkError(
            "Pilot manifest is altered or the prepared workspace has already been used"
        )
    if any((vault / dataset / "test.csv").exists() for dataset in meta["profile"]["datasets"]):
        raise BenchmarkError("holdout data was materialized before Pilot execution")
    for dataset, dataset_meta in meta["datasets"].items():
        for split in ("train", "dev"):
            path = work / "public" / dataset / f"{split}.csv"
            if file_digest(path) != dataset_meta["files"][split]["sha256"]:
                raise BenchmarkError("prepared public split changed after manifest freeze")
    if isinstance(recovery, dict):
        origin = read_json(work / "recovery_origin.json")
        events = ledger(work)
        if (
            origin.get("origin_manifest_sha256") != recovery.get("origin_manifest_sha256")
            or origin.get("origin_ledger_sha256") != recovery.get("origin_ledger_sha256")
            or file_digest(work / "recovery_origin.json") != recovery.get("recovery_origin_sha256")
            or not events
            or events[0].get("kind") != "recovery_origin"
            or any(
                row["kind"] == "agent_call_started"
                and row["call_id"]
                not in {done["call_id"] for done in events if done["kind"] == "agent_call_finished"}
                for row in events
            )
        ):
            raise BenchmarkError("recovery lineage does not match its immutable origin receipt")
        if recovery.get("single_step_target"):
            target = recovery["single_step_target"]
            prior_ids = recovery.get("prior_call_ids")
            prior_counts = recovery.get("prior_call_attempts_by_cell", {})
            target_key = (
                f"{target.get('arm')}__{target.get('dataset')}__{target.get('seed')}"
                if isinstance(target, dict)
                else ""
            )
            if (
                origin.get("prior_call_ids") != prior_ids
                or not isinstance(prior_ids, list)
                or any(not isinstance(call_id, str) for call_id in prior_ids)
                or len(prior_ids) != len(set(prior_ids))
                or len(prior_ids) != sum(int(value) for value in prior_counts.values())
                or recovery.get("single_step_calls_per_invocation") != 1
                or target != {"arm": "memory_only", "dataset": "wdbc", "seed": 42}
                or target_key not in prior_counts
            ):
                raise BenchmarkError(
                    "single-step recovery target or cumulative counters are invalid"
                )
    elif events:
        raise BenchmarkError("fresh Pilot workspace contains unexpected prior activity")
    return meta, work, vault


def prepare_pilot_recovery(
    *, root: Path, origin_root: Path, t1_call: Callable[..., dict[str, Any]]
) -> dict[str, Any]:
    """Create a linked recovery manifest without altering the failed origin."""
    origin_work = origin_root / "pilot_work"
    origin_vault = origin_root / "pilot_vault"
    origin_meta = read_json(origin_work / "meta.json")
    origin_summary = read_json(origin_root / "summary.json")
    origin_events = ledger(origin_work)
    origin_starts = [row for row in origin_events if row["kind"] == "agent_call_started"]
    origin_finishes = {
        row["call_id"]: row for row in origin_events if row["kind"] == "agent_call_finished"
    }
    origin_terminals = {
        (row["arm"], row["dataset"], row["seed"]): row
        for row in origin_events
        if row["kind"] == "cell_terminal"
    }
    origin_trials = [row for row in origin_events if row["kind"] == "started"]
    required_cells = {tuple(row) for row in origin_meta["execution_order"]}
    calls_per_cell: dict[str, int] = {}
    processes_per_cell: dict[str, int] = {}
    for cell in required_cells:
        arm, dataset, seed = cell
        key = f"{arm}__{dataset}__{seed}"
        matching = [
            row for row in origin_starts if (row["arm"], row["dataset"], row["seed"]) == cell
        ]
        if len(matching) != 1 or matching[0]["call_id"] not in origin_finishes:
            raise BenchmarkError("origin ledger is not the expected one-failure-per-cell run")
        finished = origin_finishes[matching[0]["call_id"]]
        if (
            matching[0].get("model") != "gpt-6-luna"
            or matching[0].get("model_reasoning_effort") != "max"
            or finished.get("outcome") != "failed"
            or finished.get("turn_count") != 0
            or finished.get("process_started") is not True
            or (arm, dataset, seed) not in origin_terminals
            or origin_terminals[(arm, dataset, seed)].get("outcome") != "failed"
        ):
            raise BenchmarkError("origin run contains an outcome outside the frozen recovery case")
        calls_per_cell[key] = len(matching)
        processes_per_cell[key] = int(finished.get("process_started") is True)
    if (
        origin_meta.get("profile") != STAGES["pilot"]
        or len(origin_starts) != 8
        or len(origin_terminals) != 8
        or origin_trials
        or origin_summary.get("holdout_evaluated") is not False
        or origin_summary.get("training_trials") != 0
        or origin_summary.get("model_call_attempts") != 8
        or any(
            (origin_vault / dataset / "test.csv").exists()
            for dataset in STAGES["pilot"]["datasets"]
        )
        or (origin_work / "final.started").exists()
    ):
        raise BenchmarkError("origin run is not an untouched holdout-sealed failure-only Pilot")
    origin_manifest_sha = file_digest(origin_work / "meta.json")
    origin_ledger_sha = file_digest(origin_work / "events.jsonl")
    origin_summary_sha = file_digest(origin_root / "summary.json")

    t1_call(
        "init",
        "--stage",
        "pilot",
        "--allow-agent-stage",
        "--work",
        str(root / "pilot_work"),
        "--vault",
        str(root / "pilot_vault"),
        timeout=90,
    )
    work, vault = root / "pilot_work", root / "pilot_vault"
    recovery_meta = read_json(work / "meta.json")
    recovery_vault = read_json(vault / "meta.json")
    old_vault = read_json(origin_vault / "meta.json")
    if (
        recovery_meta.get("profile") != origin_meta.get("profile")
        or recovery_meta.get("execution_order") != origin_meta.get("execution_order")
        or recovery_meta.get("datasets") != origin_meta.get("datasets")
        or recovery_vault.get("test_sha256") != old_vault.get("test_sha256")
        or recovery_vault.get("test_indices") != old_vault.get("test_indices")
    ):
        raise BenchmarkError("recovery preparation did not reproduce the frozen split and holdout")
    receipt: dict[str, Any] = {
        "origin_results_directory": origin_root.name,
        "origin_code_revision": origin_meta["code_revision"],
        "origin_manifest_sha256": origin_manifest_sha,
        "origin_ledger_sha256": origin_ledger_sha,
        "origin_summary_sha256": origin_summary_sha,
        "prior_call_attempts_by_cell": calls_per_cell,
        "prior_process_starts_by_cell": processes_per_cell,
        "prior_training_attempts_by_cell": dict.fromkeys(calls_per_cell, 0),
        "prior_terminal_outcomes_by_cell": {
            f"{arm}__{dataset}__{seed}": "failed" for arm, dataset, seed in required_cells
        },
        "provider_usage_and_cost": "unknown; no usage telemetry in origin run",
        "holdout_evaluated_in_origin": False,
    }
    atomic_json(work / "recovery_origin.json", receipt)
    recovery_meta["recovery"] = {
        "origin_manifest_sha256": origin_manifest_sha,
        "origin_ledger_sha256": origin_ledger_sha,
        "origin_summary_sha256": origin_summary_sha,
        "prior_call_attempts_by_cell": calls_per_cell,
        "prior_process_starts_by_cell": processes_per_cell,
        "recovery_origin_sha256": file_digest(work / "recovery_origin.json"),
    }
    _replace_json_file(work / "meta.json", recovery_meta)
    recovery_vault["public_meta_sha256"] = file_digest(work / "meta.json")
    _replace_json_file(vault / "meta.json", recovery_vault)
    event(
        work,
        {
            "kind": "recovery_origin",
            "origin_manifest_sha256": origin_manifest_sha,
            "origin_ledger_sha256": origin_ledger_sha,
            "prior_call_attempts": sum(calls_per_cell.values()),
            "prior_process_starts": sum(processes_per_cell.values()),
            "prior_training_attempts": 0,
        },
    )
    return {
        "origin_manifest_sha256": origin_manifest_sha,
        "origin_ledger_sha256": origin_ledger_sha,
        "origin_code_revision": origin_meta["code_revision"],
        "code_revision": recovery_meta["code_revision"],
        "prior_call_attempts": sum(calls_per_cell.values()),
        "cumulative_call_cap": 32,
        "remaining_total_call_budget": 24,
        "prior_training_attempts": 0,
        "holdout_materialized": False,
        "model_calls_started": 0,
    }


def _read_single_step_recovery_lineage(*, origin_root: Path, prior_root: Path) -> dict[str, Any]:
    """Validate every immutable ancestor and derive cumulative state from its ledgers."""
    if origin_root.is_symlink() or prior_root.is_symlink():
        raise BenchmarkError("recovery lineage roots cannot be symlinks")
    origin_root = origin_root.resolve()
    prior_root = prior_root.resolve()
    if origin_root.parent != prior_root.parent or origin_root == prior_root:
        raise BenchmarkError("recovery lineage roots must be sibling result directories")

    origin_work, origin_vault = origin_root / "pilot_work", origin_root / "pilot_vault"
    origin_meta = read_json(origin_work / "meta.json")
    origin_summary = read_json(origin_root / "summary.json")
    origin_events = ledger(origin_work)
    expected_cells = [tuple(row) for row in origin_meta.get("execution_order", [])]
    if (
        origin_meta.get("profile") != STAGES["pilot"]
        or len(expected_cells) != 8
        or len(set(expected_cells)) != 8
        or len(origin_events) == 0
        or origin_summary.get("model_call_attempts") != 8
        or origin_summary.get("training_trials") != 0
        or origin_summary.get("holdout_evaluated") is not False
        or read_json(origin_vault / "meta.json").get("public_meta_sha256")
        != file_digest(origin_work / "meta.json")
    ):
        raise BenchmarkError("root origin is not the frozen eight-call failure-only Pilot")
    if any(
        (origin_vault / name / "test.csv").exists() for name in origin_meta["profile"]["datasets"]
    ):
        raise BenchmarkError("root origin unexpectedly materialized holdout data")

    root_hashes = {
        "manifest": file_digest(origin_work / "meta.json"),
        "ledger": file_digest(origin_work / "events.jsonl"),
        "summary": file_digest(origin_root / "summary.json"),
    }
    expected_cell_keys = {
        f"{cell_arm}__{cell_dataset}__{cell_seed}"
        for cell_arm, cell_dataset, cell_seed in expected_cells
    }
    calls_by_cell = dict.fromkeys(expected_cell_keys, 0)
    processes_by_cell = dict.fromkeys(expected_cell_keys, 0)
    trials_by_cell = dict.fromkeys(expected_cell_keys, 0)
    terminal_outcomes: dict[str, str] = {}
    all_call_ids: list[str] = []
    seen_call_ids: set[str] = set()

    def consume_ledger(
        rows: list[dict[str, Any]], *, expected_call_count: int | None = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        starts = [row for row in rows if row.get("kind") == "agent_call_started"]
        finishes = [row for row in rows if row.get("kind") == "agent_call_finished"]
        finish_by_id = {row.get("call_id"): row for row in finishes}
        start_ids = [row.get("call_id") for row in starts]
        event_positions = {id(row): index for index, row in enumerate(rows)}
        if (
            len(starts) != len(start_ids)
            or None in start_ids
            or len(set(start_ids)) != len(start_ids)
            or len(finish_by_id) != len(finishes)
            or set(start_ids) != set(finish_by_id)
            or (expected_call_count is not None and len(starts) != expected_call_count)
        ):
            raise BenchmarkError("recovery lineage contains an unreconciled or duplicate call")
        for row in starts:
            arm_name, dataset_name, seed_value = row.get("arm"), row.get("dataset"), row.get("seed")
            key = f"{arm_name}__{dataset_name}__{seed_value}"
            if (
                key not in calls_by_cell
                or row.get("model") != "gpt-6-luna"
                or row.get("model_reasoning_effort") != "max"
                or finish_by_id[row["call_id"]].get("outcome") != "failed"
                or finish_by_id[row["call_id"]].get("process_started") is not True
                or finish_by_id[row["call_id"]].get("turn_count") != 0
                or event_positions[id(finish_by_id[row["call_id"]])] <= event_positions[id(row)]
            ):
                raise BenchmarkError("recovery lineage contains a non-frozen model-call outcome")
            calls_by_cell[key] += 1
            expected_ordinal = calls_by_cell[key]
            stable_id = f"{arm_name}-{dataset_name}-{seed_value}-call-{expected_ordinal}"
            if row.get("call_ordinal") != expected_ordinal or row["call_id"] != stable_id:
                raise BenchmarkError("recovery lineage reused or skipped a stable call identifier")
            if row["call_id"] in seen_call_ids:
                raise BenchmarkError("recovery lineage reused a model call identifier")
            seen_call_ids.add(row["call_id"])
            all_call_ids.append(row["call_id"])
            processes_by_cell[key] += int(
                finish_by_id[row["call_id"]].get("process_started") is True
            )

        started_trials = [row for row in rows if row.get("kind") == "started"]
        ended_trials = [row for row in rows if row.get("kind") in {"finished", "aborted"}]
        trial_ids = [row.get("id") for row in started_trials]
        if (
            None in trial_ids
            or len(trial_ids) != len(set(trial_ids))
            or {row.get("id") for row in ended_trials} != set(trial_ids)
            or len(ended_trials) != len(trial_ids)
        ):
            raise BenchmarkError("recovery lineage contains an unreconciled training attempt")
        for row in started_trials:
            key = f"{row.get('arm')}__{row.get('dataset')}__{row.get('seed')}"
            if key not in trials_by_cell:
                raise BenchmarkError("recovery lineage contains a trial outside the frozen cells")
            trials_by_cell[key] += 1

        for row in rows:
            if row.get("kind") == "cell_terminal":
                key = f"{row.get('arm')}__{row.get('dataset')}__{row.get('seed')}"
                if key not in calls_by_cell or row.get("outcome") not in {"failed", "completed"}:
                    raise BenchmarkError("recovery lineage contains an invalid terminal event")
                terminal_outcomes[key] = row["outcome"]
        return starts, started_trials, finishes

    _, origin_trials, _ = consume_ledger(origin_events, expected_call_count=8)
    origin_terminals = [row for row in origin_events if row.get("kind") == "cell_terminal"]
    if len(origin_trials) != 0 or len(origin_terminals) != 8 or len(terminal_outcomes) != 8:
        raise BenchmarkError("root origin does not contain eight failed terminal cells")
    for key in expected_cell_keys:
        if calls_by_cell[key] != 1 or terminal_outcomes.get(key) != "failed":
            raise BenchmarkError("root origin does not contain one failed call per cell")

    chain_reversed: list[Path] = []
    current = prior_root
    visited: set[Path] = set()
    while current != origin_root:
        if current in visited or current.parent != origin_root.parent:
            raise BenchmarkError("recovery lineage contains a cycle or escaped sibling directory")
        visited.add(current)
        receipt = read_json(current / "pilot_work" / "recovery_origin.json")
        parent_name = receipt.get("prior_results_directory")
        if parent_name is None:
            parent = origin_root
        elif (
            not isinstance(parent_name, str)
            or parent_name in {"", ".", ".."}
            or Path(parent_name).name != parent_name
        ):
            raise BenchmarkError("recovery lineage contains an unsafe parent directory name")
        else:
            parent_candidate = current.parent / parent_name
            if parent_candidate.is_symlink():
                raise BenchmarkError("recovery lineage parent cannot be a symlink")
            parent = parent_candidate
        parent = parent.resolve()
        if parent.parent != origin_root.parent or parent == current:
            raise BenchmarkError("recovery lineage parent is not a safe sibling result directory")
        chain_reversed.append(current)
        current = parent

    if not chain_reversed:
        raise BenchmarkError("prior results do not contain a recovery generation")
    chain = list(reversed(chain_reversed))
    previous_vault_meta = read_json(origin_vault / "meta.json")
    for current in chain:
        work, vault = current / "pilot_work", current / "pilot_vault"
        meta = read_json(work / "meta.json")
        receipt_path = work / "recovery_origin.json"
        receipt = read_json(receipt_path)
        summary = read_json(current / "summary.json")
        rows = ledger(work)
        vault_meta = read_json(vault / "meta.json")
        recovery = meta.get("recovery", {})
        if (
            meta.get("stage") != "pilot"
            or meta.get("profile") != origin_meta.get("profile")
            or meta.get("execution_order") != origin_meta.get("execution_order")
            or meta.get("datasets") != origin_meta.get("datasets")
            or vault_meta.get("public_meta_sha256") != file_digest(work / "meta.json")
            or vault_meta.get("test_sha256") != previous_vault_meta.get("test_sha256")
            or vault_meta.get("test_indices") != previous_vault_meta.get("test_indices")
            or recovery.get("recovery_origin_sha256") != file_digest(receipt_path)
            or not rows
            or rows[0].get("kind") != "recovery_origin"
            or sum(row.get("kind") == "recovery_origin" for row in rows) != 1
            or summary.get("holdout_evaluated") is not False
            or receipt.get("holdout_evaluated_in_ancestors", False) is not False
            or receipt.get("origin_results_directory") != origin_root.name
            or receipt.get("origin_manifest_sha256") != root_hashes["manifest"]
            or receipt.get("origin_ledger_sha256") != root_hashes["ledger"]
            or receipt.get("origin_summary_sha256") != root_hashes["summary"]
            or recovery.get("origin_manifest_sha256") != root_hashes["manifest"]
            or recovery.get("origin_ledger_sha256") != root_hashes["ledger"]
            or recovery.get("origin_summary_sha256", root_hashes["summary"])
            != root_hashes["summary"]
        ):
            raise BenchmarkError("recovery generation is not intact or linked to the root origin")
        if any((vault / name / "test.csv").exists() for name in meta["profile"]["datasets"]):
            raise BenchmarkError("recovery lineage unexpectedly materialized holdout data")
        previous_vault_meta = vault_meta

        parent_name = receipt.get("prior_results_directory")
        if parent_name is None:
            parent = origin_root
        elif (
            not isinstance(parent_name, str)
            or parent_name in {"", ".", ".."}
            or Path(parent_name).name != parent_name
        ):
            raise BenchmarkError("recovery lineage contains an unsafe parent directory name")
        else:
            parent = current.parent / parent_name
        parent = parent.resolve()
        if parent.parent != origin_root.parent or parent == current:
            raise BenchmarkError("recovery lineage parent is not a safe sibling result directory")
        parent_work = parent / "pilot_work"
        parent_hashes = {
            "manifest": file_digest(parent_work / "meta.json"),
            "ledger": file_digest(parent_work / "events.jsonl"),
            "summary": file_digest(parent / "summary.json"),
        }
        if parent == origin_root:
            if any(
                receipt.get(f"prior_{label}_sha256") not in (None, value)
                for label, value in parent_hashes.items()
            ):
                raise BenchmarkError("legacy recovery parent hashes do not match the origin")
        elif any(
            receipt.get(f"prior_{label}_sha256") != value for label, value in parent_hashes.items()
        ):
            raise BenchmarkError("recovery generation does not hash-link its immediate parent")

        prior_calls_before = dict(calls_by_cell)
        prior_processes_before = dict(processes_by_cell)
        prior_trials_before = dict(trials_by_cell)
        prior_terminals_before = dict(terminal_outcomes)
        prior_ids_before = sorted(all_call_ids)
        for label, expected_value in (
            ("prior_call_attempts_by_cell", prior_calls_before),
            ("prior_process_starts_by_cell", prior_processes_before),
            ("prior_training_attempts_by_cell", prior_trials_before),
            ("prior_terminal_outcomes_by_cell", prior_terminals_before),
        ):
            if receipt.get(label) != expected_value:
                raise BenchmarkError(f"recovery receipt {label} disagrees with ancestor ledgers")
            if label in recovery and recovery.get(label) != expected_value:
                raise BenchmarkError(f"recovery manifest {label} disagrees with ancestor ledgers")
        if "prior_call_ids" in receipt and receipt.get("prior_call_ids") != prior_ids_before:
            raise BenchmarkError("recovery receipt prior call IDs disagree with ancestor ledgers")
        if "prior_call_ids" in recovery and recovery.get("prior_call_ids") != prior_ids_before:
            raise BenchmarkError("recovery manifest prior call IDs disagree with ancestor ledgers")
        if (
            rows[0].get("prior_call_attempts") != sum(prior_calls_before.values())
            or rows[0].get("prior_process_starts") != sum(prior_processes_before.values())
            or rows[0].get("prior_training_attempts") != sum(prior_trials_before.values())
            or rows[0].get("origin_manifest_sha256") != root_hashes["manifest"]
            or rows[0].get("origin_ledger_sha256") != root_hashes["ledger"]
            or rows[0].get("prior_manifest_sha256") not in (None, parent_hashes["manifest"])
            or rows[0].get("prior_ledger_sha256") not in (None, parent_hashes["ledger"])
        ):
            raise BenchmarkError("recovery-origin event disagrees with validated lineage")

        local_starts, local_trials, _ = consume_ledger(rows)
        local_terminals = [row for row in rows if row.get("kind") == "cell_terminal"]
        if (
            len(local_starts) != 1
            or len(local_trials) != 0
            or (
                local_terminals
                and (
                    len(local_terminals) != 1
                    or local_terminals[0].get("outcome") != "failed"
                    or (
                        local_terminals[0].get("arm"),
                        local_terminals[0].get("dataset"),
                        local_terminals[0].get("seed"),
                    )
                    != (
                        local_starts[0].get("arm"),
                        local_starts[0].get("dataset"),
                        local_starts[0].get("seed"),
                    )
                )
            )
            or summary.get("model_call_attempts") != sum(calls_by_cell.values())
            or summary.get("training_trials") != sum(trials_by_cell.values())
        ):
            raise BenchmarkError("recovery generation is not a single failed-call checkpoint")
        target = receipt.get("single_step_target")
        if target is not None and target != ["memory_only", "wdbc", 42]:
            raise BenchmarkError("recovery generation changed the frozen single-step target")
        if (
            local_starts[0].get("arm") != "memory_only"
            or local_starts[0].get("dataset") != "wdbc"
            or local_starts[0].get("seed") != 42
        ):
            raise BenchmarkError("recovery generation contains a call for another cell")
    target_key = "memory_only__wdbc__42"
    return {
        "origin_hashes": root_hashes,
        "chain": chain,
        "prior_meta": read_json(prior_root / "pilot_work" / "meta.json"),
        "prior_summary": read_json(prior_root / "summary.json"),
        "prior_hashes": {
            "manifest": file_digest(prior_root / "pilot_work" / "meta.json"),
            "ledger": file_digest(prior_root / "pilot_work" / "events.jsonl"),
            "summary": file_digest(prior_root / "summary.json"),
        },
        "calls_by_cell": calls_by_cell,
        "processes_by_cell": processes_by_cell,
        "trials_by_cell": trials_by_cell,
        "terminal_outcomes": terminal_outcomes,
        "call_ids": sorted(all_call_ids),
        "total_calls": sum(calls_by_cell.values()),
        "target_key": target_key,
    }


def prepare_pilot_single_step_recovery(
    *,
    root: Path,
    origin_root: Path,
    prior_root: Path,
    t1_call: Callable[..., dict[str, Any]],
    arm: str,
    dataset: str,
    seed: int,
) -> dict[str, Any]:
    """Prepare a new linked workspace for exactly one explicitly resumed cell."""
    if (arm, dataset, seed) != ("memory_only", "wdbc", 42):
        raise BenchmarkError("single-step recovery is frozen to memory_only / WDBC / seed 42")
    if root.exists() and any(root.iterdir()):
        raise BenchmarkError("single-step recovery destination must be empty")

    lineage = _read_single_step_recovery_lineage(origin_root=origin_root, prior_root=prior_root)
    prior_meta = lineage["prior_meta"]
    prior_vault = prior_root / "pilot_vault"
    root_manifest_sha = lineage["origin_hashes"]["manifest"]
    root_ledger_sha = lineage["origin_hashes"]["ledger"]
    root_summary_sha = lineage["origin_hashes"]["summary"]
    prior_manifest_sha = lineage["prior_hashes"]["manifest"]
    prior_ledger_sha = lineage["prior_hashes"]["ledger"]
    prior_summary_sha = lineage["prior_hashes"]["summary"]
    prior_calls_by_cell = lineage["calls_by_cell"]
    prior_processes_by_cell = lineage["processes_by_cell"]
    prior_trials_by_cell = lineage["trials_by_cell"]
    prior_terminal_outcomes = lineage["terminal_outcomes"]
    prior_call_ids = lineage["call_ids"]
    total_prior_calls = lineage["total_calls"]
    target_key = lineage["target_key"]
    if (
        prior_trials_by_cell.get(target_key, 0) >= 2
        or prior_terminal_outcomes.get(target_key) != "failed"
        or total_prior_calls >= 32
        or prior_calls_by_cell.get(target_key, 0) >= 4
        or any(value > 4 for value in prior_calls_by_cell.values())
        or any(value > 2 for value in prior_trials_by_cell.values())
    ):
        raise BenchmarkError("single-step target does not match the frozen remaining budget")
    t1_call(
        "init",
        "--stage",
        "pilot",
        "--allow-agent-stage",
        "--work",
        str(root / "pilot_work"),
        "--vault",
        str(root / "pilot_vault"),
        timeout=90,
    )
    work, vault = root / "pilot_work", root / "pilot_vault"
    recovery_meta = read_json(work / "meta.json")
    recovery_vault = read_json(vault / "meta.json")
    old_vault = read_json(prior_vault / "meta.json")
    if (
        recovery_meta.get("profile") != prior_meta.get("profile")
        or recovery_meta.get("execution_order") != prior_meta.get("execution_order")
        or recovery_meta.get("datasets") != prior_meta.get("datasets")
        or recovery_vault.get("test_sha256") != old_vault.get("test_sha256")
        or recovery_vault.get("test_indices") != old_vault.get("test_indices")
    ):
        raise BenchmarkError("single-step preparation changed the frozen split or holdout")

    receipt: dict[str, Any] = {
        "origin_results_directory": origin_root.name,
        "origin_manifest_sha256": root_manifest_sha,
        "origin_ledger_sha256": root_ledger_sha,
        "origin_summary_sha256": root_summary_sha,
        "prior_results_directory": prior_root.name,
        "prior_manifest_sha256": prior_manifest_sha,
        "prior_ledger_sha256": prior_ledger_sha,
        "prior_summary_sha256": prior_summary_sha,
        "prior_call_attempts_by_cell": prior_calls_by_cell,
        "prior_process_starts_by_cell": prior_processes_by_cell,
        "prior_training_attempts_by_cell": prior_trials_by_cell,
        "prior_call_ids": prior_call_ids,
        "prior_terminal_outcomes_by_cell": prior_terminal_outcomes,
        "single_step_target": [arm, dataset, seed],
        "recovery_ancestor_directories": [path.name for path in lineage["chain"]],
        "single_step_calls_per_invocation": 1,
        "provider_usage_and_cost": "unknown; prior run had no usage telemetry",
        "holdout_evaluated_in_ancestors": False,
    }
    atomic_json(work / "recovery_origin.json", receipt)
    recovery_meta["recovery"] = {
        "origin_manifest_sha256": root_manifest_sha,
        "origin_ledger_sha256": root_ledger_sha,
        "origin_summary_sha256": root_summary_sha,
        "prior_manifest_sha256": prior_manifest_sha,
        "prior_ledger_sha256": prior_ledger_sha,
        "prior_summary_sha256": prior_summary_sha,
        "recovery_ancestor_directories": [path.name for path in lineage["chain"]],
        "prior_call_attempts_by_cell": prior_calls_by_cell,
        "prior_process_starts_by_cell": prior_processes_by_cell,
        "prior_training_attempts_by_cell": prior_trials_by_cell,
        "prior_call_ids": prior_call_ids,
        "prior_terminal_outcomes_by_cell": prior_terminal_outcomes,
        "single_step_target": {"arm": arm, "dataset": dataset, "seed": seed},
        "single_step_calls_per_invocation": 1,
        "recovery_origin_sha256": file_digest(work / "recovery_origin.json"),
    }
    _replace_json_file(work / "meta.json", recovery_meta)
    recovery_vault["public_meta_sha256"] = file_digest(work / "meta.json")
    _replace_json_file(vault / "meta.json", recovery_vault)
    event(
        work,
        {
            "kind": "recovery_origin",
            "origin_manifest_sha256": root_manifest_sha,
            "origin_ledger_sha256": root_ledger_sha,
            "prior_manifest_sha256": prior_manifest_sha,
            "prior_ledger_sha256": prior_ledger_sha,
            "prior_call_attempts": total_prior_calls,
            "prior_process_starts": sum(prior_processes_by_cell.values()),
            "prior_training_attempts": sum(prior_trials_by_cell.values()),
        },
    )
    return {
        "origin_manifest_sha256": root_manifest_sha,
        "prior_manifest_sha256": prior_manifest_sha,
        "prior_ledger_sha256": prior_ledger_sha,
        "code_revision": recovery_meta["code_revision"],
        "prior_call_attempts": total_prior_calls,
        "target_prior_call_attempts": prior_calls_by_cell[target_key],
        "cumulative_call_cap": 32,
        "remaining_total_call_budget": 32 - total_prior_calls,
        "target_remaining_call_budget": 4 - prior_calls_by_cell[target_key],
        "single_step_calls_per_invocation": 1,
        "holdout_materialized": False,
        "model_calls_started": 0,
    }


def run_pilot(
    *,
    root: Path,
    codex: str,
    confirm: str,
    guarded_call: Callable[..., Any],
    t1_call: Callable[..., dict[str, Any]],
    inspect: Callable[[], Any],
) -> dict[str, Any]:
    """Run frozen cells sequentially. A model attempt is never retried."""
    if confirm != "RUN_FOUR_POLICY_PILOT":
        raise BenchmarkError("four-policy Pilot requires RUN_FOUR_POLICY_PILOT")
    meta, work, vault = _load_frozen_pilot(root)
    if meta.get("recovery", {}).get("single_step_target"):
        raise BenchmarkError("single-step recovery must use the bounded pilot_step mode")
    order = meta["execution_order"]
    cells: list[dict[str, Any]] = []
    stopped_for_resource = False
    stopped_for_infrastructure = False
    recovery = meta.get("recovery", {})
    prior_call_counts = recovery.get("prior_call_attempts_by_cell", {})
    existing_terminals = {
        (row["arm"], row["dataset"], row["seed"]): row
        for row in ledger(work)
        if row["kind"] == "cell_terminal"
    }

    for arm, dataset, seed in order:
        terminal = existing_terminals.get((arm, dataset, seed))
        if terminal:
            cells.append(
                {
                    "arm": arm,
                    "dataset": dataset,
                    "seed": seed,
                    "outcome": terminal["outcome"],
                    "reason": "previously terminal cell preserved during recovery",
                    "training_attempts": sum(
                        row["kind"] == "started"
                        and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
                        for row in ledger(work)
                    ),
                    "model_calls": int(prior_call_counts.get(f"{arm}__{dataset}__{seed}", 0))
                    + sum(
                        row["kind"] == "agent_call_started"
                        and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
                        for row in ledger(work)
                    ),
                    "successful_trials": sum(
                        row["kind"] == "finished"
                        and row.get("exit_code") == 0
                        and row.get("dev_auroc") is not None
                        and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
                        for row in ledger(work)
                    ),
                }
            )
            continue
        try:
            inspect()
        except ResourceRefusal:
            stopped_for_resource = True
            break
        history: list[dict[str, Any]] = []
        notes: list[str] = []
        cell_key = f"{arm}__{dataset}__{seed}"
        call_number = int(prior_call_counts.get(cell_key, 0)) + sum(
            row["kind"] == "agent_call_started"
            and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
            for row in ledger(work)
        )
        cell_outcome = "failed"
        reason = "no successful trial"
        for trial_index in range(2):
            purpose = "decision"
            call_number += 1
            call_id = f"{arm}-{dataset}-{seed}-call-{call_number}"
            try:
                payload, _model_telemetry = model_call(
                    work=work,
                    codex=codex,
                    arm=arm,
                    dataset=dataset,
                    seed=seed,
                    call_id=call_id,
                    call_no=call_number,
                    purpose=purpose,
                    history=history,
                    notes=notes,
                    guarded_call=guarded_call,
                    max_timeout=150,
                )
                if arm in {"agent_only", "memory_only"}:
                    if set(payload) != {"candidate"}:
                        raise ValueError("candidate response must contain exactly candidate")
                    candidate = normalize_candidate(payload["candidate"])
                    decision = {
                        "selection_policy": "ordinary-model-candidate",
                        "candidate_sha256": digest(canonical(candidate)),
                    }
                    plan_proposal = None
                elif arm == "epipilot_heuristic":
                    candidate, decision = choose_heuristic(payload, history, call_number)
                    plan_proposal = decision["planner_proposal"]
                else:
                    candidate, decision = choose_conditional(payload, history, call_number)
                    plan_proposal = decision.get("planner_proposal")
                    if candidate is None:
                        _record_strategy_decision(work, arm, dataset, seed, call_number, decision)
                        if history:
                            cell_outcome, reason = (
                                "submitted",
                                "conditional policy stopped; submitted best trained candidate",
                            )
                        else:
                            cell_outcome, reason = (
                                "stopped",
                                "conditional policy returned nonpositive net information value",
                            )
                        break
                _record_strategy_decision(work, arm, dataset, seed, call_number, decision)
                candidate_hash = digest(canonical(candidate))
                if any(row["candidate_sha256"] == candidate_hash for row in history):
                    record_cell_incident(
                        work,
                        arm,
                        dataset,
                        seed,
                        "repeat",
                        "model proposed an identical candidate already attempted in this cell",
                    )
                    cell_outcome, reason = (
                        "invalid",
                        "repeated candidate proposal; no replacement attempt",
                    )
                    break
                candidate_path = root / f"candidate-{arm}-{dataset}-{seed}-{trial_index + 1}.json"
                candidate_path.write_bytes(canonical(candidate))
                trial = t1_call(
                    "trial",
                    "--work",
                    str(work),
                    "--arm",
                    arm,
                    "--dataset",
                    dataset,
                    "--seed",
                    str(seed),
                    "--candidate",
                    str(candidate_path),
                    timeout=80,
                )
                candidate_path.unlink(missing_ok=True)
                if trial.get("exit_code") != 0:
                    record_cell_incident(
                        work,
                        arm,
                        dataset,
                        seed,
                        "invalid",
                        f"T1 training attempt failed with exit code {trial.get('exit_code')}",
                    )
                    reason = "T1 training attempt failed; no automatic retry"
                    break
                row = {
                    "run_id": trial["id"],
                    "seed": seed,
                    "candidate": candidate,
                    "candidate_sha256": candidate_hash,
                    "dev_auroc": trial["dev_auroc"],
                    "seconds": trial["seconds"],
                    "metric_sha256": trial["metric_sha256"],
                }
                if plan_proposal is not None:
                    row["planner_proposal"] = plan_proposal
                history.append(row)
                if arm == "memory_only":
                    note_call_id = f"{arm}-{dataset}-{seed}-call-{call_number + 1}"
                    call_number += 1
                    try:
                        note_payload, _note_telemetry = model_call(
                            work=work,
                            codex=codex,
                            arm=arm,
                            dataset=dataset,
                            seed=seed,
                            call_id=note_call_id,
                            call_no=call_number,
                            purpose="memory_note",
                            history=history,
                            notes=notes,
                            guarded_call=guarded_call,
                            max_timeout=150,
                        )
                        note = note_payload.get("note")
                        expected_note = (
                            f"On frozen split, run {row['run_id']}, candidate sha256 "
                            f"{row['candidate_sha256']}, "
                            f"development AUROC {row['dev_auroc']:.6f}; scope=current cell only."
                        )
                        if (
                            set(note_payload) != {"note"}
                            or not isinstance(note, str)
                            or note.strip() != expected_note
                        ):
                            record_cell_incident(
                                work,
                                arm,
                                dataset,
                                seed,
                                "unsupported_claim",
                                "memory note did not exactly match its verified source measurement",
                            )
                            raise ValueError("memory note was not source-verifiable")
                        add_memory(work, arm, dataset, seed, row["run_id"], note)
                        notes.append(note.strip())
                    except ModelCallFailure as exc:
                        record_cell_incident(
                            work,
                            arm,
                            dataset,
                            seed,
                            "invalid",
                            f"memory-note call failed: {exc.classification}",
                        )
                        reason = f"memory-note call failed: {exc.classification}; no retry"
                        stopped_for_infrastructure = exc.infrastructure
                        break
                    except (BenchmarkError, ValueError) as exc:
                        record_cell_incident(
                            work,
                            arm,
                            dataset,
                            seed,
                            "invalid",
                            "memory note call or validation failed",
                        )
                        reason = str(exc)
                        break
                if trial_index == 1:
                    cell_outcome, reason = "submitted", "two bounded training attempts completed"
            except ResourceRefusal:
                stopped_for_resource = True
                reason = "whole-Mac memory guard refused a model or training launch"
                if history:
                    try:
                        select(work, arm, dataset, seed)
                        cell_outcome = "submitted"
                    except BenchmarkError:
                        cell_outcome = "failed"
                else:
                    cell_outcome = "failed"
                break
            except ModelCallFailure as exc:
                record_cell_incident(
                    work,
                    arm,
                    dataset,
                    seed,
                    "invalid",
                    f"decision rejected: {exc.classification}",
                )
                reason = f"decision failed: {exc.classification}; no retry"
                stopped_for_infrastructure = exc.infrastructure
                break
            except (BenchmarkError, ValueError, KeyError, TypeError) as exc:
                record_cell_incident(
                    work, arm, dataset, seed, "invalid", f"decision rejected: {type(exc).__name__}"
                )
                reason = f"decision failed: {type(exc).__name__}; no retry"
                break
        reconciled = _reconcile_open_cell_trials(
            work=work,
            arm=arm,
            dataset=dataset,
            seed=seed,
            t1_call=t1_call,
            inspect=inspect,
        )
        if not reconciled:
            stopped_for_resource = True
            cells.append(
                {
                    "arm": arm,
                    "dataset": dataset,
                    "seed": seed,
                    "outcome": "unreconciled",
                    "reason": "trial remains open because the guard refused reconciliation",
                    "training_attempts": sum(
                        row["kind"] == "started"
                        and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
                        for row in ledger(work)
                    ),
                    "model_calls": call_number,
                    "successful_trials": len(history),
                }
            )
            break
        if history:
            try:
                select(work, arm, dataset, seed)
                cell_outcome = "submitted"
                if "submitted" not in reason:
                    reason = reason + "; best previously trained candidate submitted"
            except BenchmarkError:
                cell_outcome = "failed"
                reason = reason + "; no validated submission could be frozen"
        elif cell_outcome == "submitted":
            cell_outcome = "failed"
            reason = "cell cannot be submitted without a validated trial"
        record_cell_terminal(work, arm, dataset, seed, cell_outcome, reason)
        cell_events = ledger(work)
        cell_trial_starts = [
            row
            for row in cell_events
            if row["kind"] == "started"
            and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
        ]
        trial_ids = {row["id"] for row in cell_trial_starts}
        measured_seconds = sum(
            float(row["seconds"])
            for row in cell_events
            if row["kind"] in {"finished", "aborted"} and row.get("id") in trial_ids
        )
        cells.append(
            {
                "arm": arm,
                "dataset": dataset,
                "seed": seed,
                "outcome": cell_outcome,
                "reason": reason,
                "training_attempts": len(cell_trial_starts),
                "model_calls": call_number,
                "successful_trials": len(history),
                "training_seconds": round(measured_seconds, 6),
                "best_dev_auroc": max((row["dev_auroc"] for row in history), default=None),
            }
        )
        if stopped_for_resource:
            break
        if stopped_for_infrastructure:
            break
    summary = status(work)
    final_result: dict[str, Any] | None = None
    expected_cells = len(meta["execution_order"])
    if (
        not stopped_for_resource
        and not stopped_for_infrastructure
        and len(cells) == expected_cells
        and summary["submitted"] == expected_cells
        and summary["model_calls_open"] == 0
    ):
        history_events = ledger(work)
        audit: dict[str, Any] = {}
        for arm, dataset, seed in meta["execution_order"]:
            key = f"{arm}__{dataset}__{seed}"
            repeats = sum(
                row.get("category") == "repeat"
                and (row.get("arm"), row.get("dataset"), row.get("seed")) == (arm, dataset, seed)
                for row in history_events
                if row["kind"] == "cell_incident"
            )
            audit[key] = {
                "human_interventions": 0,
                "repeated_experiments": repeats,
                "unsupported_claims": sum(
                    row.get("category") == "unsupported_claim"
                    and (row.get("arm"), row.get("dataset"), row.get("seed"))
                    == (arm, dataset, seed)
                    for row in history_events
                    if row["kind"] == "cell_incident"
                ),
            }
        audit_path = work / "audit.json"
        audit_path.write_bytes(canonical(audit))
        final_result = t1_call(
            "finalize",
            "--work",
            str(work),
            "--vault",
            str(vault),
            "--audit",
            str(audit_path),
            timeout=200,
        )
        summary = status(work)
    return {
        "work": str(work),
        "vault": str(vault),
        "execution_order": order,
        "cells": cells,
        "cell_count_completed_or_terminal": len(cells),
        "stopped_for_resource_guard": stopped_for_resource,
        "paused_for_infrastructure": stopped_for_infrastructure,
        "training_trials": summary["completed"],
        "model_call_attempts": summary["model_call_attempts"],
        "codex_process_starts": summary["codex_process_starts"],
        "model_call_usage": summary["model_usage"],
        "target_auroc": summary["target_auroc"],
        "target_attained_trials": summary["target_attained_trials"],
        "invalid_events": summary["invalid_events"],
        "repeated_experiments": summary["repeated_experiments"],
        "manual_interventions": 0,
        "unsupported_claims": summary["unsupported_claim_events"],
        "holdout_evaluated": summary["holdout_evaluated"],
        "pilot_run_status": (
            "paused_for_infrastructure" if stopped_for_infrastructure else "completed"
        ),
        "final_evaluation": final_result,
        "qualification": (
            "Pilot. Holdout opens only after every cell has a submitted candidate "
            "and all model calls are closed."
        ),
    }


def _single_step_history(
    work: Path, arm: str, dataset: str, seed: int
) -> tuple[list[dict[str, Any]], list[str]]:
    events = ledger(work)
    starts = {
        row["id"]: row
        for row in events
        if row["kind"] == "started"
        and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
    }
    finished = {
        row["id"]: row
        for row in events
        if row["kind"] == "finished"
        and row.get("exit_code") == 0
        and row.get("dev_auroc") is not None
        and row.get("id") in starts
    }
    history: list[dict[str, Any]] = []
    for run_id, result in finished.items():
        candidate_path = work / "runs" / run_id / "candidate.json"
        candidate = read_json(candidate_path)
        row: dict[str, Any] = {
            "run_id": run_id,
            "seed": seed,
            "candidate": candidate,
            "candidate_sha256": file_digest(candidate_path),
            "dev_auroc": result["dev_auroc"],
            "seconds": result["seconds"],
            "metric_sha256": result["metric_sha256"],
        }
        history.append(row)
    notes = [
        row["note"]
        for row in events
        if row["kind"] == "memory_note"
        and row.get("arm") == arm
        and row.get("dataset") == dataset
        and row.get("seed") == seed
    ]
    return history, notes


def run_pilot_single_step(
    *,
    root: Path,
    codex: str,
    confirm: str,
    guarded_call: Callable[..., Any],
    t1_call: Callable[..., dict[str, Any]],
    inspect: Callable[[], Any],
    arm: str,
    dataset: str,
    seed: int,
) -> dict[str, Any]:
    """Run at most one model call and at most one trial for the frozen recovery cell."""
    if confirm != "RUN_ONE_PILOT_STEP":
        raise BenchmarkError("single-cell Pilot recovery requires RUN_ONE_PILOT_STEP")
    meta, work, vault = _load_frozen_pilot(root)
    recovery = meta.get("recovery", {})
    target = recovery.get("single_step_target")
    if target != {"arm": arm, "dataset": dataset, "seed": seed}:
        raise BenchmarkError("requested cell differs from the frozen single-step target")
    if (arm, dataset, seed) != ("memory_only", "wdbc", 42):
        raise BenchmarkError("single-step recovery is frozen to memory_only / WDBC / seed 42")
    if (work / "final.started").exists() or any(
        (vault / name / "test.csv").exists() for name in meta["profile"]["datasets"]
    ):
        raise BenchmarkError("single-step recovery cannot proceed after holdout release")
    existing_calls = [row for row in ledger(work) if row["kind"] == "agent_call_started"]
    if any(row["kind"] == "cell_terminal" for row in ledger(work)):
        raise BenchmarkError("single-step recovery cell is already terminal")
    open_calls = {row["call_id"] for row in existing_calls} - {
        row["call_id"] for row in ledger(work) if row["kind"] == "agent_call_finished"
    }
    if open_calls:
        raise BenchmarkError("previous single-step model call is unreconciled; do not repeat")

    history, notes = _single_step_history(work, arm, dataset, seed)
    trial_starts = [
        row
        for row in ledger(work)
        if row["kind"] == "started"
        and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
    ]
    prior_key = f"{arm}__{dataset}__{seed}"
    prior_calls = int(recovery.get("prior_call_attempts_by_cell", {}).get(prior_key, 0))
    prior_trials = int(recovery.get("prior_training_attempts_by_cell", {}).get(prior_key, 0))
    call_number = (
        prior_calls
        + sum(
            row["kind"] == "agent_call_started"
            and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
            for row in ledger(work)
        )
        + 1
    )
    purpose = "decision"
    if arm == "memory_only" and history:
        known_notes = {
            row.get("id")
            for row in ledger(work)
            if row["kind"] == "memory_note"
            and (row.get("arm"), row.get("dataset"), row.get("seed")) == (arm, dataset, seed)
        }
        if history[-1]["run_id"] not in known_notes:
            purpose = "memory_note"
    if (
        purpose == "decision"
        and prior_trials + len(trial_starts) >= meta["profile"]["per_cell_trials"]
    ):
        raise BenchmarkError("per-cell training budget exhausted; no further decision call")
    call_id = f"{arm}-{dataset}-{seed}-call-{call_number}"
    try:
        inspect()
        payload, usage = model_call(
            work=work,
            codex=codex,
            arm=arm,
            dataset=dataset,
            seed=seed,
            call_id=call_id,
            call_no=call_number,
            purpose=purpose,
            history=history,
            notes=notes,
            guarded_call=guarded_call,
            max_timeout=150,
        )
    except ModelCallFailure as exc:
        record_cell_incident(
            work,
            arm,
            dataset,
            seed,
            "invalid",
            f"single-step {purpose} failed: {exc.classification}",
        )
        finish = next(
            (
                row
                for row in reversed(ledger(work))
                if row["kind"] == "agent_call_finished" and row.get("call_id") == call_id
            ),
            {},
        )
        return {
            "cell": [arm, dataset, seed],
            "call_id": call_id,
            "purpose": purpose,
            "model_calls_this_invocation": 1,
            "call_outcome": "failed",
            "failure_classification": exc.classification,
            "pilot_run_status": "paused_for_infrastructure" if exc.infrastructure else "failed",
            "training_trials_this_invocation": 0,
            "holdout_evaluated": False,
            "call_diagnostic": {
                key: finish.get(key)
                for key in (
                    "elapsed_seconds",
                    "exit_code",
                    "failure_classification",
                    "stderr_excerpt_redacted",
                    "stdout_event_types",
                    "stdout_error_events",
                    "usage_available",
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                )
                if key in finish
            },
        }
    except ResourceRefusal as exc:
        call_was_reserved = any(
            row["kind"] == "agent_call_started" and row.get("call_id") == call_id
            for row in ledger(work)
        )
        return {
            "cell": [arm, dataset, seed],
            "call_id": call_id if call_was_reserved else None,
            "purpose": purpose,
            "model_calls_this_invocation": int(call_was_reserved),
            "call_outcome": "not_started",
            "failure_classification": "resource_guard_refusal",
            "safe_reason": str(exc),
            "pilot_run_status": "paused_for_resource_guard",
            "training_trials_this_invocation": 0,
            "holdout_evaluated": False,
            "usage_available": False,
        }

    if purpose == "memory_note":
        latest = history[-1]
        expected_note = (
            f"On frozen split, run {latest['run_id']}, candidate sha256 "
            f"{latest['candidate_sha256']}, development AUROC {latest['dev_auroc']:.6f}; "
            "scope=current cell only."
        )
        note = payload.get("note")
        if set(payload) != {"note"} or note != expected_note:
            record_cell_incident(
                work,
                arm,
                dataset,
                seed,
                "unsupported_claim",
                "memory note did not exactly match its verified source measurement",
            )
            return {
                "cell": [arm, dataset, seed],
                "call_id": call_id,
                "purpose": purpose,
                "model_calls_this_invocation": 1,
                "call_outcome": "invalid_output",
                "pilot_run_status": "failed",
                "training_trials_this_invocation": 0,
                "holdout_evaluated": False,
                "usage": usage,
            }
        add_memory(work, arm, dataset, seed, latest["run_id"], note)
        checkpoint = {
            "cell": [arm, dataset, seed],
            "call_id": call_id,
            "purpose": purpose,
            "status": "memory_note_saved",
            "run_id": latest["run_id"],
            "candidate_sha256": latest["candidate_sha256"],
            "dev_auroc": latest["dev_auroc"],
        }
        atomic_json(work / "checkpoints" / f"{prior_key}-call-{call_number}.json", checkpoint)
        event(work, {"kind": "cell_checkpoint", **checkpoint})
        return {
            **checkpoint,
            "model_calls_this_invocation": 1,
            "training_trials_this_invocation": 0,
            "holdout_evaluated": False,
            "usage": usage,
            "pilot_run_status": "checkpointed",
        }

    if set(payload) != {"candidate"}:
        raise BenchmarkError("memory_only decision must contain exactly one candidate")
    candidate = normalize_candidate(payload["candidate"])
    candidate_hash = digest(canonical(candidate))
    if any(row["candidate_sha256"] == candidate_hash for row in history):
        record_cell_incident(
            work,
            arm,
            dataset,
            seed,
            "repeat",
            "single-step proposal repeats a candidate already attempted in this cell",
        )
        return {
            "cell": [arm, dataset, seed],
            "call_id": call_id,
            "purpose": purpose,
            "model_calls_this_invocation": 1,
            "call_outcome": "repeated_candidate",
            "pilot_run_status": "failed",
            "training_trials_this_invocation": 0,
            "holdout_evaluated": False,
            "usage": usage,
        }
    decision = {
        "selection_policy": "ordinary-model-candidate",
        "candidate_sha256": candidate_hash,
        "single_step_checkpoint": True,
    }
    _record_strategy_decision(work, arm, dataset, seed, call_number, decision)
    checkpoint_dir = work / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    candidate_checkpoint = checkpoint_dir / f"{prior_key}-call-{call_number}.candidate.json"
    atomic_json(candidate_checkpoint, candidate)
    candidate_path = root / f"candidate-{arm}-{dataset}-{seed}-{call_number}.json"
    atomic_json(candidate_path, candidate)
    trials_before = len(trial_starts)
    try:
        inspect()
        trial = t1_call(
            "trial",
            "--work",
            str(work),
            "--arm",
            arm,
            "--dataset",
            dataset,
            "--seed",
            str(seed),
            "--candidate",
            str(candidate_path),
            timeout=80,
        )
    except (BenchmarkError, ResourceRefusal) as exc:
        checkpoint = {
            "cell": [arm, dataset, seed],
            "call_id": call_id,
            "purpose": purpose,
            "status": "training_interrupted",
            "candidate_sha256": candidate_hash,
            "candidate_path": str(candidate_checkpoint),
            "failure_classification": type(exc).__name__,
        }
        atomic_json(checkpoint_dir / f"{prior_key}-call-{call_number}.json", checkpoint)
        event(work, {"kind": "cell_checkpoint", **checkpoint})
        return {
            **checkpoint,
            "model_calls_this_invocation": 1,
            "training_trials_this_invocation": max(
                0,
                sum(
                    row["kind"] == "started"
                    and (row["arm"], row["dataset"], row["seed"]) == (arm, dataset, seed)
                    for row in ledger(work)
                )
                - trials_before,
            ),
            "holdout_evaluated": False,
            "usage": usage,
            "pilot_run_status": "training_failed",
        }
    finally:
        candidate_path.unlink(missing_ok=True)
    if trial.get("exit_code") != 0:
        record_cell_incident(
            work,
            arm,
            dataset,
            seed,
            "invalid",
            f"single-step T1 trial failed with exit code {trial.get('exit_code')}",
        )
        checkpoint = {
            "cell": [arm, dataset, seed],
            "call_id": call_id,
            "purpose": purpose,
            "status": "training_failed",
            "candidate_sha256": candidate_hash,
            "candidate_path": str(candidate_checkpoint),
            "trial_id": trial.get("id"),
            "training_exit_code": trial.get("exit_code"),
        }
        atomic_json(checkpoint_dir / f"{prior_key}-call-{call_number}.json", checkpoint)
        event(work, {"kind": "cell_checkpoint", **checkpoint})
        return {
            **checkpoint,
            "model_calls_this_invocation": 1,
            "training_trials_this_invocation": 1,
            "holdout_evaluated": False,
            "usage": usage,
            "pilot_run_status": "training_failed",
        }
    checkpoint = {
        "cell": [arm, dataset, seed],
        "call_id": call_id,
        "purpose": purpose,
        "status": "trial_validated",
        "candidate": candidate,
        "candidate_sha256": candidate_hash,
        "candidate_path": str(candidate_checkpoint),
        "trial_id": trial["id"],
        "dev_auroc": trial["dev_auroc"],
        "training_seconds": trial["seconds"],
        "metric_sha256": trial["metric_sha256"],
        "next_step": "memory_note",
    }
    atomic_json(checkpoint_dir / f"{prior_key}-call-{call_number}.json", checkpoint)
    event(
        work,
        {"kind": "cell_checkpoint", **{k: v for k, v in checkpoint.items() if k != "candidate"}},
    )
    return {
        **checkpoint,
        "model_calls_this_invocation": 1,
        "training_trials_this_invocation": 1,
        "holdout_evaluated": False,
        "usage": usage,
        "pilot_run_status": "checkpointed",
    }
