from __future__ import annotations

import fcntl
import hashlib
import os
import platform
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from epipilot.core.events import EventType
from epipilot.engineering.models import (
    ManagedEvent,
    ManagedPolicy,
    MilestoneSource,
    PublicationDue,
    WorkLease,
    WorkStatus,
)
from epipilot.research.engine import ResearchEngine
from epipilot.runtime.event_store import EventStore


class ManagedWork:
    def __init__(self, store: EventStore, project_id: str, root: Path):
        self.store, self.project_id, self.root = store, project_id, root.resolve()
        self.common = Path(
            self.git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        )

    @staticmethod
    def git(cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise ValueError(f"Git {args[0]} failed: {result.stderr.strip()[:500]}")
        return result.stdout.strip()

    @contextmanager
    def locked(self) -> Iterator[ResearchEngine]:
        with (self.common / "epipilot-managed.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield ResearchEngine(self.store, self.project_id, self.common)

    @property
    def policy(self) -> ManagedPolicy:
        engine = ResearchEngine(self.store, self.project_id, self.common)
        return self._policy(engine)

    def _policy(self, engine: ResearchEngine) -> ManagedPolicy:
        for event in reversed(engine.state.engineering):
            if event.policy is not None:
                p = event.policy
                if Path(p.repository) != self.root or Path(p.common_dir) != self.common:
                    raise ValueError("managed repository identity mismatch")
                return p
        raise ValueError("explicit work configure authorization required")

    @staticmethod
    def _work(engine: ResearchEngine, operation: str) -> WorkLease:
        for event in reversed(engine.state.engineering):
            if event.work is not None and event.work.operation_id == operation:
                return event.work
        raise KeyError(operation)

    @staticmethod
    def _save(engine: ResearchEngine, work: WorkLease, **changes: object) -> WorkLease:
        updated = WorkLease.model_validate({**work.model_dump(), **changes})
        engine.append(EventType.MANAGED_WORK, ManagedEvent(work=updated))
        return updated

    def configure(self, policy: ManagedPolicy) -> None:
        with self.locked() as engine:
            if engine.state.engineering:
                if self._policy(engine) == policy:
                    return
                raise ValueError("managed policy is immutable; configure a new project")
            if Path(policy.repository) != self.root or Path(policy.common_dir) != self.common:
                raise ValueError("policy repository/common-dir mismatch")
            if not policy.integration_branch.startswith("epipilot/"):
                raise ValueError("integration branch must be dedicated epipilot/*")
            self.git(self.root, "check-ref-format", "refs/heads/" + policy.integration_branch)
            self.git(self.root, "check-ref-format", policy.publication_ref)
            if not policy.publication_ref.startswith("refs/heads/"):
                raise ValueError("publication requires exact branch ref")
            if (
                not policy.remote
                or policy.remote.startswith("-")
                or self.git(self.root, "remote", "get-url", "--push", policy.remote)
                != policy.remote_url
            ):
                raise ValueError("fixed remote identity mismatch")
            workroot = Path(policy.worktree_root)
            if not workroot.is_absolute() or self.root == workroot or self.root in workroot.parents:
                raise ValueError("worktrees must be outside user repository")
            if any(not command or not command[0] for command in policy.acceptance):
                raise ValueError("explicit acceptance argv required")
            self._exclusive(policy, allow_missing=True)
            engine.append(EventType.MANAGED_WORK, ManagedEvent(policy=policy))

    def _managed_path(self, policy: ManagedPolicy, path: Path, role: str) -> Path:
        root = Path(policy.worktree_root)
        if root.is_symlink():
            raise ValueError("managed worktree root must not be a symlink")
        root = root.resolve()
        candidate = (
            root / path.relative_to(root)
            if path.is_absolute() and path.is_relative_to(root)
            else path
        )
        if not candidate.is_absolute() or candidate.is_symlink():
            raise ValueError(f"{role} path must not be a symlink")
        resolved = candidate.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"{role} path escapes managed worktree root")
        for parent in candidate.parents:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError(f"{role} path has symlinked parent")
        if candidate.exists() or candidate.is_symlink():
            common = Path(
                self.git(candidate, "rev-parse", "--path-format=absolute", "--git-common-dir")
            )
            if common != self.common:
                raise ValueError(f"{role} belongs to another Git repository")
            records = self.git(self.root, "worktree", "list", "--porcelain").split("\n\n")
            if not any(
                record.startswith("worktree " + str(candidate) + "\n") for record in records
            ):
                raise ValueError(f"{role} is not a registered managed worktree")
        return candidate

    def _exclusive(self, policy: ManagedPolicy, *, allow_missing: bool = False) -> Path:
        target = self._managed_path(
            policy, Path(policy.worktree_root) / "integration", "integration"
        )
        records = self.git(self.root, "worktree", "list", "--porcelain").split("\n\n")
        for record in records:
            lines = record.splitlines()
            if "branch refs/heads/" + policy.integration_branch in lines and (
                not lines or lines[0] != "worktree " + str(target)
            ):
                raise ValueError("refusing user checkout integration branch")
        if target.exists() and not allow_missing:
            if (
                self.git(target, "symbolic-ref", "HEAD")
                != "refs/heads/" + policy.integration_branch
            ):
                raise ValueError("integration worktree identity mismatch")
            if self.git(target, "status", "--porcelain", "--untracked-files=all"):
                raise ValueError("integration worktree polluted")
        return target

    def _integration(self, policy: ManagedPolicy) -> Path:
        target = self._exclusive(policy)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            ref = "refs/heads/" + policy.integration_branch
            exists = (
                subprocess.run(
                    ["git", "-C", str(self.root), "show-ref", "--verify", "--quiet", ref],
                    check=False,
                ).returncode
                == 0
            )
            if exists:
                self.git(self.root, "worktree", "add", str(target), policy.integration_branch)
            else:
                self.git(
                    self.root,
                    "worktree",
                    "add",
                    "-b",
                    policy.integration_branch,
                    str(target),
                    "HEAD",
                )
        return target

    def acquire(
        self, operation: str, scope: tuple[str, ...], forbidden: tuple[str, ...] = ()
    ) -> WorkLease:
        if not operation.strip():
            raise ValueError("stable operation id required")
        for path in (*scope, *forbidden):
            self._safe_path(path)
        with self.locked() as engine:
            policy = self._policy(engine)
            key = hashlib.sha256((self.project_id + ":" + operation).encode()).hexdigest()[:24]
            if operation.startswith("report:"):
                key = "reports-" + hashlib.sha256(self.project_id.encode()).hexdigest()[:16]
            requested_path = Path(policy.worktree_root) / key
            if requested_path.is_symlink():
                raise ValueError("task path must not be a symlink")
            if requested_path.exists():
                try:
                    common = Path(
                        self.git(
                            requested_path,
                            "rev-parse",
                            "--path-format=absolute",
                            "--git-common-dir",
                        )
                    )
                except ValueError:
                    raise ValueError("task path is not a managed Git worktree") from None
                if common != self.common:
                    raise ValueError("task path belongs to another Git repository")
            try:
                work = self._work(engine, operation)
                if work.scope != scope or work.forbidden != forbidden:
                    raise ValueError("operation scope cannot change")
                if work.status is not WorkStatus.ACQUIRING:
                    return work
            except KeyError:
                integration = self._integration(policy)
                base = self.git(integration, "rev-parse", "HEAD")
                if operation.startswith("report:"):
                    key = "reports-" + hashlib.sha256(self.project_id.encode()).hexdigest()[:16]
                    if any(
                        w.operation_id.startswith("report:") and not w.released
                        for w in self.status()
                    ):
                        raise ValueError(
                            "report workspace requires recovery/release before next round"
                        ) from None
                work = WorkLease(
                    operation_id=operation,
                    cwd=str(Path(policy.worktree_root) / key),
                    base=base,
                    scope=scope,
                    forbidden=forbidden,
                    status=WorkStatus.ACQUIRING,
                )
                engine.append(EventType.MANAGED_WORK, ManagedEvent(work=work))
            cwd = self._managed_path(policy, Path(work.cwd), "task")
            if not cwd.exists():
                self.git(self.root, "worktree", "add", "--detach", str(cwd), work.base)
            if self.git(cwd, "rev-parse", "HEAD") != work.base:
                raise ValueError("acquire HEAD drift")
            return self._save(engine, work, status=WorkStatus.ACTIVE)

    @staticmethod
    def _safe_path(path: str) -> None:
        parts = PurePosixPath(path).parts
        if (
            not parts
            or path.startswith(("/", "-"))
            or ".." in parts
            or any(
                p == ".git"
                or p.startswith(".env")
                or p.endswith((".key", ".pem"))
                or p in {"id_rsa", "id_ed25519"}
                for p in parts
            )
        ):
            raise ValueError("unsafe scope/path")

    def _check_scope(self, work: WorkLease, paths: list[str]) -> None:
        for path in paths:
            self._safe_path(path)
            if not any(
                path == p or path.startswith(p.rstrip("/") + "/") for p in work.scope
            ) or any(path == p or path.startswith(p.rstrip("/") + "/") for p in work.forbidden):
                raise ValueError("scope violation: " + path)
            full = Path(work.cwd) / path
            if full.is_symlink() or Path(work.cwd) not in full.resolve().parents:
                raise ValueError("symlink/path escape")

    def _commit(self, cwd: Path, tree: str, parents: tuple[str, ...], message: str) -> str:
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(("-p", parent))
        return self.git(cwd, *args, "-m", message)

    def complete(self, operation: str) -> WorkLease:
        with self.locked() as engine:
            policy = self._policy(engine)
            work = self._work(engine, operation)
            if work.status in {WorkStatus.INTEGRATED, WorkStatus.PUBLISHING, WorkStatus.PUSHED}:
                return work
            cwd = Path(work.cwd)
            if (
                Path(self.git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                != self.common
            ):
                raise ValueError("task repository identity drift")
            symbolic = subprocess.run(
                ["git", "-C", str(cwd), "symbolic-ref", "-q", "HEAD"],
                capture_output=True,
                check=False,
            )
            if symbolic.returncode != 1:
                raise ValueError("task lease must retain detached HEAD; refusing user branch")
            if not work.task_commit:
                if self.git(cwd, "rev-parse", "HEAD") != work.base:
                    raise ValueError("task HEAD drift")
                paths = set(
                    self.git(cwd, "diff", "--name-only", "-z", "--no-renames", work.base).split(
                        "\0"
                    )
                ) | set(
                    self.git(cwd, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
                )
                paths |= set(
                    self.git(
                        cwd, "diff", "--cached", "--name-only", "-z", "--no-renames", work.base
                    ).split("\0")
                )
                changed = sorted(p for p in paths if p)
                self._check_scope(work, changed)
                if not changed:
                    raise ValueError("no scoped changes")
                work = self._save(engine, work, status=WorkStatus.COMMITTING)
                self.git(cwd, "add", "--", *changed)
                tree = self.git(cwd, "write-tree")
                task = self._commit(cwd, tree, (work.base,), "feat(managed): " + operation)
                committed_paths = self.git(
                    cwd, "diff", "--name-only", "-z", "--no-renames", work.base, task
                ).split("\0")
                self._check_scope(work, [p for p in committed_paths if p])
                if self.git(cwd, "rev-parse", task + "^") != work.base:
                    raise ValueError("committed parent drift")
                work = self._save(engine, work, status=WorkStatus.COMMITTED, task_commit=task)
            head = self.git(cwd, "rev-parse", "HEAD")
            if head == work.base:
                self.git(cwd, "update-ref", "HEAD", work.task_commit, work.base)
            elif head != work.task_commit:
                raise ValueError("task HEAD drift")
            if self.git(cwd, "status", "--porcelain", "--untracked-files=all"):
                raise ValueError("task changed after commit")
            integration = self._integration(policy)
            base = self.git(integration, "rev-parse", "HEAD")
            if work.status is WorkStatus.INTEGRATING and base == work.candidate:
                return self._save(
                    engine, work, status=WorkStatus.INTEGRATED, integrated_commit=base
                )
            environment_hash = hashlib.sha256(
                (
                    platform.platform()
                    + sys.version
                    + sys.executable
                    + self.git(self.root, "--version")
                    + os.environ.get("PATH", "")
                ).encode()
            ).hexdigest()
            if (
                not work.candidate
                or work.integration_base != base
                or work.environment_hash != environment_hash
            ):
                try:
                    tree = self.git(
                        self.root, "merge-tree", "--write-tree", base, work.task_commit
                    ).splitlines()[0]
                except ValueError as exc:
                    self._save(
                        engine, work, status=WorkStatus.BLOCKED, reason="integration conflict"
                    )
                    raise ValueError("integration conflict; lease preserved") from exc
                candidate = self._commit(
                    self.root,
                    tree,
                    (base, work.task_commit),
                    "feat(managed): integrate " + operation,
                )
                profile_hash = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
                work = self._save(
                    engine,
                    work,
                    status=WorkStatus.VERIFYING,
                    candidate=candidate,
                    candidate_tree=tree,
                    integration_base=base,
                    profile_hash=profile_hash,
                    environment_hash=environment_hash,
                )
            if work.status not in {WorkStatus.VERIFIED, WorkStatus.INTEGRATING}:
                gate = self._managed_path(
                    policy, Path(policy.worktree_root) / ("gate-" + work.candidate), "gate"
                )
                if not gate.exists():
                    self.git(self.root, "worktree", "add", "--detach", str(gate), work.candidate)
                if self.git(gate, "rev-parse", "HEAD") != work.candidate or self.git(
                    gate, "status", "--porcelain", "--untracked-files=all"
                ):
                    raise ValueError("gate worktree polluted")
                try:
                    for argv in policy.acceptance:
                        result = subprocess.run(
                            argv,
                            cwd=gate,
                            capture_output=True,
                            timeout=policy.timeout_seconds,
                            check=False,
                        )
                        if result.returncode:
                            raise ValueError("engineering gate failed")
                    if (
                        self.git(gate, "status", "--porcelain", "--untracked-files=all")
                        or self.git(gate, "rev-parse", "HEAD") != work.candidate
                        or self.git(gate, "write-tree") != work.candidate_tree
                    ):
                        raise ValueError("engineering gate polluted candidate")
                except (ValueError, subprocess.TimeoutExpired) as exc:
                    self._save(
                        engine,
                        work,
                        status=WorkStatus.BLOCKED,
                        reason="engineering gate failed or polluted",
                    )
                    raise ValueError("engineering gate failed or polluted") from exc
                work = self._save(engine, work, status=WorkStatus.VERIFIED)
            self._exclusive(policy)
            if self.git(integration, "rev-parse", "HEAD") != work.integration_base:
                raise ValueError("integration base drift; retry to rebuild candidate")
            work = self._save(engine, work, status=WorkStatus.INTEGRATING)
            self.git(integration, "merge", "--ff-only", work.candidate)
            return self._save(
                engine, work, status=WorkStatus.INTEGRATED, integrated_commit=work.candidate
            )

    def status(self) -> tuple[WorkLease, ...]:
        engine = ResearchEngine(self.store, self.project_id, self.common)
        latest = {
            e.work.operation_id: e.work for e in engine.state.engineering if e.work is not None
        }
        return tuple(latest.values())

    def publish(self, operation: str, artifact_refs: tuple[str, ...]) -> WorkLease:
        with self.locked() as engine:
            policy = self._policy(engine)
            work = self._work(engine, operation)
            if work.status is WorkStatus.PUSHED:
                return work
            if (
                work.status not in {WorkStatus.INTEGRATED, WorkStatus.PUBLISHING}
                or not artifact_refs
            ):
                raise ValueError(
                    "publication requires integrated engineering milestone and "
                    "checkpoint/artifact refs"
                )
            if any(
                w.status in {WorkStatus.COMMITTING, WorkStatus.VERIFYING, WorkStatus.INTEGRATING}
                for w in self.status()
            ):
                raise ValueError("unrecovered operation blocks publication")
            if (
                self.git(self.root, "remote", "get-url", "--push", policy.remote)
                != policy.remote_url
            ):
                raise ValueError("fixed remote identity changed")
            sha = work.integrated_commit

            def remote_sha() -> str:
                line = self.git(
                    self.root, "ls-remote", "--refs", policy.remote_url, policy.publication_ref
                )
                return line.split()[0] if line else ""

            self.validate_references(engine, work, artifact_refs)
            work = self._save(
                engine, work, status=WorkStatus.PUBLISHING, artifact_refs=artifact_refs
            )
            current = remote_sha()
            if current != sha and current:
                self.git(self.root, "fetch", "--no-tags", policy.remote_url, policy.publication_ref)
                contains = (
                    subprocess.run(
                        ["git", "-C", str(self.root), "merge-base", "--is-ancestor", sha, current],
                        check=False,
                    ).returncode
                    == 0
                )
                if contains:
                    return self._save(
                        engine,
                        work,
                        status=WorkStatus.PUSHED,
                        artifact_refs=artifact_refs,
                        publication_result=(
                            "already-contained; remote ref was an existing descendant"
                        ),
                    )
                ff = (
                    subprocess.run(
                        ["git", "-C", str(self.root), "merge-base", "--is-ancestor", current, sha],
                        check=False,
                    ).returncode
                    == 0
                )
                if not ff:
                    raise ValueError("remote diverged; normal fast-forward only")
            if current != sha:
                work = self._save(
                    engine, work, status=WorkStatus.PUBLISHING, artifact_refs=artifact_refs
                )
                try:
                    self.git(
                        self.root,
                        "push",
                        "--",
                        policy.remote_url,
                        sha + ":" + policy.publication_ref,
                    )
                except (ValueError, subprocess.TimeoutExpired):
                    if remote_sha() != sha:
                        raise
                if remote_sha() != sha:
                    raise ValueError("remote publication not confirmed")
            return self._save(
                engine,
                work,
                status=WorkStatus.PUSHED,
                artifact_refs=artifact_refs,
                publication_result="pushed-exact-sha",
            )

    def validate_references(
        self, engine: ResearchEngine, work: WorkLease, refs: tuple[str, ...]
    ) -> None:
        state = engine.state
        for ref in refs:
            if ref == "work:" + work.operation_id and work.integrated_commit:
                self.git(self.root, "cat-file", "-e", work.integrated_commit + "^{commit}")
                continue
            if ref.startswith("artifact:"):
                path = ref.removeprefix("artifact:")
                self._safe_path(path)
                self.git(self.root, "cat-file", "-e", work.integrated_commit + ":" + path)
                continue
            if ref.startswith("evidence:") and ref[9:] in {str(e.id) for e in state.evidence}:
                continue
            if ref.startswith("event:"):
                identifier = ref[6:]
                allowed = {
                    EventType.BUDGET_SETTLED,
                    EventType.EXPERIMENT_REVIEWED,
                    EventType.RESEARCH_QUESTION_RESOLVED,
                }
                if any(
                    str(e.id) == identifier and e.type in allowed
                    for e in self.store.load(self.project_id)
                ):
                    continue
            raise ValueError("unknown or unvalidated milestone reference: " + ref)

    def push_due(self) -> tuple[PublicationDue, ...]:
        events = self.store.load(self.project_id)
        research_refs = tuple(
            "event:" + str(e.id)
            for e in events
            if e.type
            in {
                EventType.BUDGET_SETTLED,
                EventType.EXPERIMENT_REVIEWED,
                EventType.RESEARCH_QUESTION_RESOLVED,
            }
        )
        due: list[PublicationDue] = []
        for work in self.status():
            if work.status not in {WorkStatus.INTEGRATED, WorkStatus.PUBLISHING}:
                continue
            if work.operation_id.startswith("report:"):
                # Round reports alone are not scientific milestones.
                if not research_refs:
                    continue
                source = MilestoneSource.STAGE_REPORT
                refs = ("work:" + work.operation_id, research_refs[-1])
            else:
                source = MilestoneSource.ENGINEERING_DELIVERY
                refs = ("work:" + work.operation_id, "work:" + work.operation_id)
            due.append(
                PublicationDue(
                    operation_id=work.operation_id,
                    source=source,
                    references=refs,
                    commit=work.integrated_commit,
                )
            )
        return tuple(due)

    def release(self, operation: str) -> WorkLease:
        with self.locked() as engine:
            policy = self._policy(engine)
            work = self._work(engine, operation)
            if work.released:
                return work
            if work.status not in {WorkStatus.INTEGRATED, WorkStatus.PUSHED}:
                raise ValueError("release requires integrated work; failed/active work retained")
            integration = self._exclusive(policy)
            self.git(integration, "merge-base", "--is-ancestor", work.integrated_commit, "HEAD")
            key = hashlib.sha256((self.project_id + ":" + operation).encode()).hexdigest()[:24]
            if operation.startswith("report:"):
                key = "reports-" + hashlib.sha256(self.project_id.encode()).hexdigest()[:16]
            expected = self._managed_path(policy, Path(policy.worktree_root) / key, "task")
            if Path(work.cwd) != expected:
                raise ValueError("refusing unowned worktree")
            targets = [
                (expected, work.task_commit),
                (
                    self._managed_path(
                        policy, Path(policy.worktree_root) / ("gate-" + work.candidate), "gate"
                    ),
                    work.candidate,
                ),
            ]
            for target, commit in targets:
                if not target.exists():
                    continue
                if (
                    target.is_symlink()
                    or Path(
                        self.git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")
                    )
                    != self.common
                ):
                    raise ValueError("unowned worktree")
                if self.git(target, "rev-parse", "HEAD") != commit:
                    raise ValueError("worktree HEAD drift")
                if self.git(target, "status", "--porcelain", "--untracked-files=all", "--ignored"):
                    raise ValueError("dirty worktree retained")
                symbolic = subprocess.run(
                    ["git", "-C", str(target), "symbolic-ref", "-q", "HEAD"],
                    capture_output=True,
                    check=False,
                )
                if symbolic.returncode != 1:
                    raise ValueError("refusing checkout branch cleanup")
            if any(
                w.operation_id != operation and not w.released and w.cwd == work.cwd
                for w in self.status()
            ):
                raise ValueError("active work shares workspace")
            work = self._save(engine, work, release_intent=True)
            for target, _ in targets:
                if target.exists():
                    self.git(self.root, "worktree", "remove", str(target))
            return self._save(engine, work, released=True)
