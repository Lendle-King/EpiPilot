"""Hermes native-plugin frontend and bounded executor policy for EpiPilot."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from epipilot.core.events import EventType
from epipilot.core.models import Provenance
from epipilot.events.codec import make_project_event
from epipilot.events.payloads import RequirementAddedPayload
from epipilot.integrations.hermes.runtime_contract import (
    EXECUTOR_ALLOWED_NATIVE_TOOLS,
    EXECUTOR_CHILD_ENV,
    EXECUTOR_GUARD_TOOL,
    EXECUTOR_TOOLSET,
    EXECUTOR_WORKSPACE_ENV,
)
from epipilot.requirements.models import (
    ProjectContract,
    RequirementKind,
    new_requirement_id,
)
from epipilot.runtime.sqlite_event_store import SqliteEventStore
from epipilot.state.project import ProjectState
from epipilot.state.replay import replay_project

logger = logging.getLogger(__name__)

_ACTIVE_PROJECT_KEY = "active_project_id"
_LAST_PROJECT_KEY = "last_project_id"
_EVENT_DB_NAME = "epipilot-events.sqlite3"


class HermesPluginState(Protocol):
    """Minimal profile-scoped state surface used by the integration."""

    @property
    def data_dir(self) -> Path:
        """Return the profile-scoped plugin data directory."""
        ...

    def get(self, key: str, default: object | None = None) -> object:
        """Read a JSON-serializable plugin-state value."""
        ...

    def set(self, key: str, value: object) -> None:
        """Atomically replace one plugin-state value."""
        ...


SlashHandler = Callable[[str], str | None]
HookHandler = Callable[..., object]
ToolHandler = Callable[..., object]
AvailabilityCheck = Callable[[], bool]


class HermesPluginContext(Protocol):
    """Subset of the Hermes PluginContext contract required by EpiPilot."""

    state: HermesPluginState

    def register_command(
        self,
        name: str,
        *,
        handler: SlashHandler,
        description: str,
    ) -> None:
        """Register one in-session slash command."""
        ...

    def register_hook(self, name: str, callback: HookHandler) -> None:
        """Register one lifecycle hook callback."""
        ...

    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, object],
        handler: ToolHandler,
        check_fn: AvailabilityCheck | None = None,
    ) -> None:
        """Register one plugin tool used as the bounded child sentinel."""
        ...


class HermesFrontendError(RuntimeError):
    """Fail-closed integration error safe to surface to the operator."""


@dataclass(slots=True)
class HermesFrontendBridge:
    """Profile-scoped Hermes interface backed by EpiPilot canonical events."""

    state: HermesPluginState

    def event_store(self) -> SqliteEventStore:
        """Resolve the canonical event store from the active Hermes profile."""
        return SqliteEventStore(Path(self.state.data_dir) / _EVENT_DB_NAME)

    def active_project_id(self) -> str | None:
        """Return the active project pointer, rejecting malformed plugin state."""
        raw = self.state.get(_ACTIVE_PROJECT_KEY, None)
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw.strip():
            raise HermesFrontendError("active project pointer is malformed")
        return raw

    def last_project_id(self) -> str | None:
        raw = self.state.get(_LAST_PROJECT_KEY, None)
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw.strip():
            raise HermesFrontendError("last project pointer is malformed")
        return raw

    def start_project(self, goal: str) -> ProjectState:
        """Create a canonical project goal and activate that project."""
        goal = goal.strip()
        if not goal:
            raise HermesFrontendError("project goal must not be empty")
        if self.active_project_id() is not None:
            raise HermesFrontendError(
                "an EpiPilot project is already active; run /epipilot exit first"
            )

        project_id = str(uuid4())
        provenance = Provenance(source="hermes:user", scope="project-intake")
        payload = RequirementAddedPayload(
            requirement_id=new_requirement_id(),
            kind=RequirementKind.GOAL,
            statement=goal,
            provenance_source=provenance.source,
            provenance_scope=provenance.scope,
            provenance_created_at=provenance.created_at,
        )
        store = self.event_store()
        store.append(
            make_project_event(EventType.REQUIREMENT_ADDED, project_id, payload),
            expected_version=0,
        )
        self.state.set(_ACTIVE_PROJECT_KEY, project_id)
        self.state.set(_LAST_PROJECT_KEY, project_id)
        return self.load_project(project_id)

    def add_requirement(self, kind: RequirementKind, statement: str) -> ProjectState:
        """Append one explicit user-authored requirement to the active project."""
        if kind is RequirementKind.GOAL:
            raise HermesFrontendError("the canonical goal cannot be replaced implicitly")
        statement = statement.strip()
        if not statement:
            raise HermesFrontendError("requirement statement must not be empty")

        project = self.require_active_project()
        provenance = Provenance(source="hermes:user", scope="project-intake")
        payload = RequirementAddedPayload(
            requirement_id=new_requirement_id(),
            kind=kind,
            statement=statement,
            provenance_source=provenance.source,
            provenance_scope=provenance.scope,
            provenance_created_at=provenance.created_at,
        )
        store = self.event_store()
        store.append(
            make_project_event(
                EventType.REQUIREMENT_ADDED,
                project.project_id,
                payload,
            ),
            expected_version=project.event_version,
        )
        return self.load_project(project.project_id)

    def exit_project(self) -> str:
        """Leave EpiPilot mode without mutating canonical project history."""
        project_id = self.active_project_id()
        if project_id is None:
            raise HermesFrontendError("no EpiPilot project is active")
        self.state.set(_LAST_PROJECT_KEY, project_id)
        self.state.set(_ACTIVE_PROJECT_KEY, None)
        return project_id

    def resume_project(self, project_id: str) -> ProjectState:
        """Bind the Hermes profile to an existing replayable EpiPilot project."""
        project_id = project_id.strip()
        if not project_id:
            raise HermesFrontendError("project id must not be empty")
        if self.active_project_id() is not None:
            raise HermesFrontendError(
                "an EpiPilot project is already active; run /epipilot exit first"
            )

        project = self.load_project(project_id)
        self.state.set(_ACTIVE_PROJECT_KEY, project_id)
        self.state.set(_LAST_PROJECT_KEY, project_id)
        return project

    def load_project(self, project_id: str) -> ProjectState:
        store = self.event_store()
        events = store.load(project_id)
        if not events:
            raise HermesFrontendError(f"unknown EpiPilot project: {project_id}")
        try:
            project = replay_project(project_id, events)
            self._contract(project)
        except Exception as exc:
            raise HermesFrontendError(
                "canonical project stream failed validation; refusing to enter EpiPilot mode"
            ) from exc
        return project

    def require_active_project(self) -> ProjectState:
        project_id = self.active_project_id()
        if project_id is None:
            raise HermesFrontendError("no EpiPilot project is active; run /epipilot start <goal>")
        return self.load_project(project_id)

    @staticmethod
    def _contract(project: ProjectState) -> ProjectContract:
        return ProjectContract(
            project_id=project.project_id,
            requirements=project.requirements,
            decisions=project.decisions,
        )


_HELP = """\
/epipilot — use Hermes as the human-facing frontend for EpiPilot

Commands:
  /epipilot start <goal>          Create and activate a canonical EpiPilot project
  /epipilot success <criterion>   Add an explicit success criterion
  /epipilot constrain <rule>      Add an explicit hard constraint
  /epipilot status                Show canonical project status
  /epipilot resume <project-id>   Re-enter an existing project
  /epipilot exit                  Leave EpiPilot mode without deleting state
  /epipilot help                  Show this help

The interactive Hermes session remains an interface only. Coding execution is launched by
EpiPilot TaskRuntime through a bounded HermesExecutor child and independently verified before
any task may become PASSED.
"""


def _split_subcommand(raw_args: str) -> tuple[str, str]:
    raw = raw_args.strip()
    if not raw:
        return "help", ""
    subcommand, separator, remainder = raw.partition(" ")
    return subcommand.lower(), remainder.strip() if separator else ""


def _format_project(project: ProjectState) -> str:
    contract = ProjectContract(
        project_id=project.project_id,
        requirements=project.requirements,
        decisions=project.decisions,
    )
    goal = next(
        requirement.statement
        for requirement in project.requirements
        if requirement.kind is RequirementKind.GOAL
    )
    success = tuple(
        requirement.statement
        for requirement in project.requirements
        if requirement.kind is RequirementKind.SUCCESS_CRITERION
    )
    constraints = tuple(
        requirement.statement
        for requirement in project.requirements
        if requirement.kind is RequirementKind.HARD_CONSTRAINT
    )
    task_counts: dict[str, int] = {}
    for task in project.tasks:
        task_counts[task.status.value] = task_counts.get(task.status.value, 0) + 1

    lines = [
        "[EpiPilot] mode: active",
        f"project: {project.project_id}",
        f"canonical event version: {project.event_version}",
        f"goal: {goal}",
        f"execution ready: {'yes' if contract.execution_ready else 'no'}",
        f"success criteria: {len(success)}",
    ]
    lines.extend(f"  - {item}" for item in success)
    lines.append(f"hard constraints: {len(constraints)}")
    lines.extend(f"  - {item}" for item in constraints)
    lines.append(f"tasks: {len(project.tasks)}")
    if task_counts:
        lines.append(
            "task states: "
            + ", ".join(f"{name}={count}" for name, count in sorted(task_counts.items()))
        )
    lines.append("Hermes frontend: interface-only; bounded child execution is EpiPilot-owned")
    return "\n".join(lines)


def _build_mode_context(project: ProjectState) -> str:
    return (
        "EpiPilot mode is active. Hermes is the human-facing interface only; "
        "EpiPilot's replayed event stream is canonical project truth.\n\n"
        f"{_format_project(project)}\n\n"
        "Do not treat conversation history, your own claims, or executor self-report as "
        "canonical facts. Never claim a task PASSED from self-report. The interactive "
        "Hermes session must not execute repository tools directly; EpiPilot TaskRuntime "
        "launches bounded HermesExecutor children and independently verifies their changes. "
        "Continue requirements discussion with the user. Record explicit success criteria "
        "with `/epipilot success <criterion>` and hard constraints with "
        "`/epipilot constrain <rule>`."
    )


def _executor_child_active() -> bool:
    return os.environ.get(EXECUTOR_CHILD_ENV, "").strip() == "1"


def _executor_workspace() -> Path | None:
    raw = os.environ.get(EXECUTOR_WORKSPACE_ENV, "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _path_is_within_workspace(raw_path: str, workspace: Path) -> bool:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(workspace)
    except (OSError, ValueError):
        return False
    return ".git" not in relative.parts


def _executor_child_tool_policy(tool_name: object, args_obj: object) -> object:
    """Fail-closed file-only policy for a HermesExecutor child."""
    if tool_name == EXECUTOR_GUARD_TOOL:
        return None
    if not isinstance(tool_name, str) or tool_name not in EXECUTOR_ALLOWED_NATIVE_TOOLS:
        return _block(
            "EpiPilot HermesExecutor permits only bounded file tools. Terminal/process, "
            "network, delegation, memory mutation, and other tools are outside this task's authority."
        )
    if not isinstance(args_obj, dict):
        return _block("EpiPilot could not validate Hermes tool arguments; blocked fail-closed.")

    workspace = _executor_workspace()
    if workspace is None:
        return _block("EpiPilot executor workspace binding is missing or invalid; blocked fail-closed.")

    # Hermes' native cross-profile escape is intentionally unavailable inside a bounded child.
    if args_obj.get("cross_profile") not in (None, False):
        return _block("EpiPilot HermesExecutor does not permit cross-profile file access.")

    if tool_name == "patch":
        mode = args_obj.get("mode", "replace")
        if mode != "replace":
            return _block(
                "EpiPilot HermesExecutor currently permits patch mode='replace' only; "
                "multi-file patch payloads are blocked until every embedded path is contract-validated."
            )

    raw_path = args_obj.get("path", "." if tool_name == "search_files" else None)
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _block("EpiPilot requires an explicit valid path for this file operation.")
    if not _path_is_within_workspace(raw_path, workspace):
        return _block(
            "EpiPilot blocked a file operation outside the authenticated executor workspace "
            "or inside Git metadata."
        )
    return None


def _make_command_handler(bridge: HermesFrontendBridge) -> SlashHandler:
    def handle(raw_args: str) -> str:
        subcommand, argument = _split_subcommand(raw_args)
        try:
            if subcommand in {"help", "-h", "--help"}:
                return _HELP
            if subcommand == "start":
                if not argument:
                    return "Usage: /epipilot start <goal>"
                return _format_project(bridge.start_project(argument))
            if subcommand == "success":
                if not argument:
                    return "Usage: /epipilot success <criterion>"
                return _format_project(
                    bridge.add_requirement(RequirementKind.SUCCESS_CRITERION, argument)
                )
            if subcommand in {"constrain", "constraint"}:
                if not argument:
                    return "Usage: /epipilot constrain <rule>"
                return _format_project(
                    bridge.add_requirement(RequirementKind.HARD_CONSTRAINT, argument)
                )
            if subcommand == "status":
                project_id = bridge.active_project_id()
                if project_id is None:
                    last = bridge.last_project_id()
                    suffix = (
                        f"\nlast project: {last}\nresume with /epipilot resume {last}"
                        if last is not None
                        else ""
                    )
                    return "[EpiPilot] mode: inactive" + suffix
                return _format_project(bridge.require_active_project())
            if subcommand == "resume":
                if not argument or " " in argument:
                    return "Usage: /epipilot resume <project-id>"
                return _format_project(bridge.resume_project(argument))
            if subcommand in {"exit", "pause"}:
                project_id = bridge.exit_project()
                return (
                    f"[EpiPilot] mode: inactive\nproject preserved: {project_id}\n"
                    f"resume with /epipilot resume {project_id}"
                )
            return f"Unknown subcommand: {subcommand}\n\n{_HELP}"
        except HermesFrontendError as exc:
            return f"[EpiPilot] refused: {exc}"
        except Exception:
            logger.exception("Hermes frontend integration failed closed")
            return (
                "[EpiPilot] canonical state could not be loaded safely; the operation was refused."
            )

    return handle


def _make_pre_llm_hook(bridge: HermesFrontendBridge) -> HookHandler:
    def pre_llm_call(**_: object) -> object:
        if _executor_child_active():
            return {
                "context": (
                    "You are an EpiPilot-owned bounded HermesExecutor child. Use only the "
                    "file tools exposed for this run and remain inside the bound workspace. "
                    "Your completion claim is a non-authoritative observation; EpiPilot "
                    "performs independent verification after you stop."
                )
            }
        try:
            project_id = bridge.active_project_id()
            if project_id is None:
                return None
            project = bridge.require_active_project()
            return {"context": _build_mode_context(project)}
        except Exception:
            logger.exception("EpiPilot pre_llm_call failed closed")
            return {
                "context": (
                    "EpiPilot mode metadata is present but canonical state is unavailable. "
                    "Do not execute tools, mutate the repository, or claim completion. "
                    "Ask the operator to inspect `/epipilot status` or leave mode with "
                    "`/epipilot exit`."
                )
            }

    return pre_llm_call


def _make_pre_tool_hook(bridge: HermesFrontendBridge) -> HookHandler:
    def pre_tool_call(**kwargs: object) -> object:
        if _executor_child_active():
            return _executor_child_tool_policy(kwargs.get("tool_name"), kwargs.get("args"))
        try:
            if bridge.active_project_id() is None:
                return None
        except Exception:
            return _block(
                "EpiPilot frontend state is malformed or unavailable; tool execution is blocked fail-closed."
            )
        return _block(
            "The interactive EpiPilot Hermes session is interface-only. Tool execution must "
            "be initiated by EpiPilot TaskRuntime through the bounded HermesExecutor and "
            "independent verification."
        )

    return pre_tool_call


def _executor_guard_tool(_args: object, **_: object) -> str:
    """Capability sentinel proving the EpiPilot child policy plugin is loaded."""
    return "EpiPilot HermesExecutor guard is active."


def register(ctx: HermesPluginContext) -> None:
    """Register the Hermes frontend while keeping EpiPilot as canonical authority."""
    bridge = HermesFrontendBridge(state=ctx.state)
    ctx.register_command(
        "epipilot",
        handler=_make_command_handler(bridge),
        description="Enter and inspect EpiPilot evidence-driven project mode.",
    )
    ctx.register_hook("pre_llm_call", _make_pre_llm_hook(bridge))
    ctx.register_hook("pre_tool_call", _make_pre_tool_hook(bridge))
    ctx.register_tool(
        name=EXECUTOR_GUARD_TOOL,
        toolset=EXECUTOR_TOOLSET,
        schema={
            "name": EXECUTOR_GUARD_TOOL,
            "description": (
                "Internal EpiPilot executor-policy sentinel. Do not call this tool during normal work."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        handler=_executor_guard_tool,
        check_fn=_executor_child_active,
    )
