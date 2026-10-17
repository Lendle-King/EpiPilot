"""Claim-grounded experiment route planning and iteration records.

A route proposes the next algorithm experiments and must cite existing claim
ids as its basis (fail closed on unknown ids). Execution results return as a
review record; claim updates themselves still flow through the grounded
semantic pipeline after the experiment report lands in the research repository.
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from epipilot.workspace.chatgpt_web import extract_route_review_json
from epipilot.workspace.onboarding import WorkspaceStore, _now
from epipilot.workspace.research_agent import ResearchOnboardingAgent

ROUTE_GUIDANCE = (
    "You are a research planner for China-listed ETF daily allocation research. "
    "Given the project goal and a set of source-grounded claims (each with an id), "
    "propose the next best ALGORITHM experiment route. Requirements: "
    "(1) every proposal must list basis_claim_ids that genuinely support its "
    "rationale; never cite unrelated claims; unknown ids are rejected later. "
    "(2) premises_zh must state the market-level premises derived from market_ "
    "claims that the route relies on. "
    "(3) Write title, rationale_zh, design_zh, success_criteria_zh and "
    "falsification_zh in Simplified Chinese. "
    "(4) Every experiment must be executable under the existing point-in-time "
    "discipline: specify data window, method variants, controls and cost "
    "assumptions. "
    "(5) stop_rules_zh must align with pre-registered gates (no retuning frozen "
    "parameters, no backfilled forward evidence). "
    "(6) Never promise returns or claim statistical significance before testing. "
    "(7) Prefer experiments that reduce the largest unknowns first. "
    "(8) When a canonical V2.5 input is unavailable but the repository contains "
    "a documented public-data track, choose a bounded data-readiness or diagnostic "
    "route that can run with the files actually present; state that it is exploratory, "
    "must not treat public diagnostics as V2.5 evidence, and do not repeat a route "
    "that only requests missing artifacts. "
    "(9) A diagnostic route may validate coverage, joins, ledger invariants or an "
    "explicitly non-canonical public-track backtest, but it must not infer historical "
    "membership, backfill forward observations, or relax a frozen benchmark gate. "
    "Return ONLY a JSON object matching the schema."
)

_ROUTE_SCHEMA_NOTE = (
    '{"premises_zh": ["..."], "stop_rules_zh": ["..."], "proposals": '
    '[{"title": "...", "rationale_zh": "...", "basis_claim_ids": ["<claim id>"], '
    '"design_zh": "...", "success_criteria_zh": "...", "falsification_zh": "..."}]} '
    "Use exactly these fields and no others. Every proposal must include all six "
    "fields. Keep min lengths: title >= 6, rationale_zh and design_zh >= 10, "
    "success_criteria_zh and falsification_zh >= 6 characters."
)


class RouteModel(Protocol):
    """Minimal model surface needed for route generation."""

    def complete_json(self, system: str, prompt: str) -> Any: ...


class ChatGPTWebPort(Protocol):
    """Browser-driver surface: one prompt in, one raw reply out."""

    def submit(self, prompt: str) -> str: ...


def build_iteration_pack(route: dict[str, Any], project_name: str) -> str:
    """Build the one-shot ChatGPT iteration pack for a generated route.

    The reply contract is deliberately compact: long final streams are cut by
    the ChatGPT transport ("message transmission timed out"), so the model is
    instructed to answer with the JSON only and a bounded report body.
    """
    trimmed = {
        key: value
        for key, value in route.items()
        if key in {"premises_zh", "proposals", "stop_rules_zh"}
    }
    return "\n\n".join(
        [
            "EpiPilot experiment route iteration for " + project_name + ".",
            "ROUTE_RUN_ID: " + str(route.get("run_id", "")),
            "If you already executed these experiments earlier in this conversation, "
            "reuse those results; do not redo them.",
            "Step 1 - Evaluate this route for feasibility, data availability and statistical risk.",
            "Hard boundaries: Do not invoke GitHub/Notion remote actions, trigger workflows, "
            "push commits, or place trades. Do not modify frozen V2.5 parameters or backfill "
            "forward evidence. If execution requires a forbidden action, report blocked.",
            "Step 2 - Execute the proposed experiments (write code, run backtests). "
            "Keep any long workings in your tool activity; do not narrate them in "
            "the final message.",
            "Step 3 - Reply with ONLY one compact JSON object matching: "
            '{"verdict":"continue|goal_met|blocked","evaluation_zh":"<max 400 chars>",'
            '"results_digest_zh":"<max 600 chars>","recommended_next_zh":"<max 300 chars>",'
            '"evidence_refs":["path-or-url"],"report_markdown":"<max 3000 chars>"}',
            "Use verdict goal_met when the phase objective is reached, blocked when a "
            "major problem makes further rounds pointless, otherwise continue. "
            "report_markdown must be a concise experiment report summary (max 3000 "
            "characters) - long streams get cut by the transport. All narrative "
            "fields in Simplified Chinese. Never claim significance before testing; "
            "never promise returns. Do not invoke GitHub/Notion remote actions.",
            "ROUTE:\n" + json.dumps(trimmed, ensure_ascii=False, indent=2),
        ]
    )


class ExperimentProposal(BaseModel):
    # Unknown keys from the model are ignored; semantic constraints stay strict.
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=6, max_length=200)
    rationale_zh: str = Field(min_length=10, max_length=800)
    basis_claim_ids: list[str] = Field(min_length=1, max_length=32)
    design_zh: str = Field(min_length=10, max_length=1200)
    success_criteria_zh: str = Field(min_length=6, max_length=400)
    falsification_zh: str = Field(min_length=6, max_length=400)


class ExperimentRoute(BaseModel):
    # Unknown keys from the model are ignored; semantic constraints stay strict.
    model_config = ConfigDict(extra="ignore")

    premises_zh: list[str] = Field(max_length=20)
    proposals: list[ExperimentProposal] = Field(min_length=1, max_length=8)
    stop_rules_zh: list[str] = Field(max_length=20)


class RouteReview(BaseModel):
    """ChatGPT-side evaluation + execution digest; never canonical facts."""

    # Unknown keys are ignored; required fields and limits stay strict.
    model_config = ConfigDict(extra="ignore")

    verdict: str = Field(min_length=2, max_length=40)
    evaluation_zh: str = Field(min_length=10, max_length=2000)
    results_digest_zh: str = Field(min_length=10, max_length=2000)
    recommended_next_zh: str = Field(min_length=6, max_length=800)
    evidence_refs: list[str] = Field(max_length=12)
    report_markdown: str = Field(max_length=50_000, default="")


def _route_prompt(report: dict[str, Any]) -> str:
    claims = list(report.get("claims", []))

    def rank(claim: dict[str, Any]) -> tuple[int, str]:
        topic = str(claim.get("topic_key", ""))
        important = topic.startswith(("market_", "data_")) or claim.get("kind") in {
            "lesson",
            "unknown",
            "hypothesis",
        }
        return (0 if important else 1, topic)

    selected = sorted(claims, key=rank)[:60]
    lines = [
        "Grounded claims (id -> kind/topic/proposition):",
    ]
    for claim in selected:
        text = str(claim.get("proposition", ""))[:220]
        lines.append(
            f"- {claim.get('id')} | {claim.get('kind')} | {claim.get('topic_key')} | {text}"
        )
    lines.append("Complete claim-id inventory (copy only these exact IDs):")
    lines.append("- " + ", ".join(str(claim.get("id")) for claim in claims))
    conflicts = report.get("potential_conflicts") or []
    if conflicts:
        lines.append("Potential conflicts: " + json.dumps(conflicts, ensure_ascii=False))
    lines.append(f"Claim pool size: {len(claims)} total.")
    lines.append("Return JSON exactly like: " + _ROUTE_SCHEMA_NOTE)
    lines.append(
        "IMPORTANT: every basis_claim_ids entry must be copied character-for-character "
        "from an id in the list above; never invent, shorten or modify an id. Prefer "
        "3-6 ids per proposal that you can copy exactly."
    )
    return "\n".join(lines)


class ExperimentRoutePlanner:
    """Generates and stores route/iteration records for one workspace project."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store
        with self.store._db() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS route_runs ("
                "run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
                "kind TEXT NOT NULL, payload_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )

    def generate(self, project_id: str, model: RouteModel, provider: str) -> dict[str, Any]:
        report = ResearchOnboardingAgent(self.store).report(project_id)
        if not report.get("available"):
            raise ValueError("No grounded claims available; run semantic analysis first")
        known = {str(claim.get("id")) for claim in report.get("claims", [])}
        prompt = _route_prompt(report)
        route, problem = self._validated_route(model, prompt, known)
        if route is None:
            corrective = (
                prompt
                + "\n\nCORRECTIVE CONSTRAINT: a previous attempt failed validation ("
                + problem
                + "). Return ONLY the exact schema fields (no extras), respect the "
                "minimum lengths, and copy every basis_claim_ids entry "
                "character-for-character from the claim list above; never invent or "
                "modify ids. The complete allowed claim-id inventory is: "
                + ", ".join(sorted(known))
                + "."
            )
            route, problem = self._validated_route(model, corrective, known)
        if route is None:
            raise ValueError(f"Route output failed local validation: {problem}")
        self._persist(
            project_id,
            "route",
            {"route": route.model_dump(), "provider": provider},
        )
        return self.report(project_id)

    @staticmethod
    def _validated_route(
        model: RouteModel, prompt: str, known_claim_ids: set[str]
    ) -> tuple[ExperimentRoute | None, str]:
        payload = model.complete_json(ROUTE_GUIDANCE, prompt)
        try:
            route = ExperimentRoute.model_validate(payload)
        except ValidationError as error:
            return None, f"schema validation failed: {error}"[:400]
        missing = sorted(
            {
                claim_id.strip()
                for proposal in route.proposals
                for claim_id in proposal.basis_claim_ids
                if claim_id.strip() not in known_claim_ids
            }
        )
        if missing:
            return None, f"unknown claim ids: {missing[:3]}"
        return route, ""

    def import_review(self, project_id: str, raw: str) -> dict[str, Any]:
        if len(raw.encode()) > 70_000:
            raise ValueError("Route review exceeds 70 KiB")
        try:
            review = RouteReview.model_validate_json(raw)
        except ValidationError as error:
            raise ValueError("Route review failed local schema validation") from error
        self._persist(project_id, "review", {"review": review.model_dump()})
        return self.report(project_id)

    def record_loop_summary(self, project_id: str, summary: dict[str, Any]) -> None:
        """Persist one autonomous-iteration summary as an auditable record."""
        self.store.get(project_id)
        self._persist(project_id, "loop_summary", {"summary": summary})

    def record_loop_resume(self, project_id: str, route_run_id: str, round_index: int) -> None:
        """Append recovery provenance without replacing the original route or stop."""
        self.store.get(project_id)
        self._persist(
            project_id, "loop_resume", {"route_run_id": route_run_id, "round": round_index}
        )

    def record_loop_checkpoint(self, project_id: str, checkpoint: dict[str, Any]) -> None:
        """Persist the state of the last fully completed iteration round."""
        self.store.get(project_id)
        self._persist(project_id, "loop_checkpoint", {"checkpoint": checkpoint})

    def load_loop_checkpoint(self, project_id: str) -> dict[str, Any] | None:
        self.store.get(project_id)
        with self.store._db() as conn:
            row = conn.execute(
                "SELECT payload_json FROM route_runs WHERE project_id=? "
                "AND kind='loop_checkpoint' ORDER BY rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        checkpoint = payload.get("checkpoint")
        return dict(checkpoint) if isinstance(checkpoint, dict) else None

    def run_chatgpt_iteration(
        self, project_id: str, driver: ChatGPTWebPort, project_name: str
    ) -> dict[str, Any]:
        """Drive ChatGPT Web with the current route and import the review reply.

        The reply is only accepted through the same local schema validation as
        the manual paste path; claim updates still flow through the grounded
        semantic pipeline after the experiment report lands in the repository.
        """
        report = self.report(project_id)
        if not report.get("available") or not report.get("route"):
            raise ValueError("No experiment route available; generate a route first")
        pack = build_iteration_pack(dict(report["route"]), project_name)
        response = driver.submit(pack)
        return self.import_review(project_id, extract_route_review_json(response))

    def report(self, project_id: str) -> dict[str, Any]:
        self.store.get(project_id)  # fail closed on missing project
        with self.store._db() as conn:
            rows = conn.execute(
                "SELECT kind, payload_json FROM route_runs WHERE project_id=? ORDER BY rowid ASC",
                (project_id,),
            ).fetchall()
        route: dict[str, Any] | None = None
        review: dict[str, Any] | None = None
        for kind, payload_json in rows:
            payload = json.loads(payload_json)
            if kind == "route":
                route = {
                    **dict(payload["route"]),
                    "run_id": payload["run_id"],
                    "created_at": payload["created_at"],
                    "provider": payload.get("provider"),
                }
            elif kind == "review":
                review = {
                    **dict(payload["review"]),
                    "run_id": payload["run_id"],
                    "created_at": payload["created_at"],
                }
        return {"available": route is not None, "route": route, "review": review}

    def _persist(self, project_id: str, kind: str, payload: dict[str, Any]) -> None:
        record = {"run_id": str(uuid4()), "created_at": _now(), **payload}
        with self.store._db() as conn:
            conn.execute(
                "INSERT INTO route_runs VALUES (?, ?, ?, ?, ?)",
                (
                    str(record["run_id"]),
                    project_id,
                    kind,
                    json.dumps(record, ensure_ascii=False),
                    str(record["created_at"]),
                ),
            )
            self.store._event(
                conn,
                project_id,
                "route_" + kind + "_recorded",
                {"run_id": str(record["run_id"]), "kind": kind},
            )
