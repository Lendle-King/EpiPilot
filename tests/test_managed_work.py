import subprocess
import sys
from pathlib import Path

import pytest

from epipilot.engineering.models import ManagedPolicy, WorkStatus
from epipilot.engineering.service import ManagedWork
from epipilot.runtime.sqlite_event_store import SqliteEventStore
from epipilot.state.replay import replay_project


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def managed(tmp_path: Path) -> tuple[ManagedWork, Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "app.txt").write_text("initial\n")
    git(root, "add", "app.txt")
    git(root, "commit", "-m", "initial")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(root, "remote", "add", "origin", str(remote))
    service = ManagedWork(SqliteEventStore(tmp_path / "state.sqlite3"), "project", root)
    service.configure(
        ManagedPolicy(
            repository=str(root),
            common_dir=str(root / ".git"),
            worktree_root=str(tmp_path / "work"),
            remote="origin",
            remote_url=str(remote),
            publication_ref="refs/heads/delivery",
            acceptance=(
                (sys.executable, "-c", "from pathlib import Path; assert Path('app.txt').exists()"),
            ),
        )
    )
    return service, root, remote


def test_delivery_preserves_user_index_and_replays(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, root, remote = managed
    (root / "app.txt").write_text("user staged\n")
    git(root, "add", "app.txt")
    (root / "private-note").write_text("untracked\n")
    before = git(root, "status", "--porcelain=v1")
    head = git(root, "rev-parse", "HEAD")
    lease = service.acquire("task-1", ("app.txt",))
    assert service.acquire("task-1", ("app.txt",)) == lease
    (Path(lease.cwd) / "app.txt").write_text("delivered\n")
    result = service.complete("task-1")
    assert result.status is WorkStatus.INTEGRATED
    assert service.complete("task-1") == result
    assert git(root, "status", "--porcelain=v1") == before
    assert git(root, "rev-parse", "HEAD") == head
    assert (root / "private-note").read_text() == "untracked\n"
    published = service.publish("task-1", ("artifact:app.txt",))
    assert published.status is WorkStatus.PUSHED
    assert service.publish("task-1", ("artifact:app.txt",)) == published
    assert git(remote, "rev-parse", "refs/heads/delivery") == result.integrated_commit
    state = replay_project("project", service.store.load("project"))
    assert state.research is None
    assert state.engineering[-1].work == published


@pytest.mark.parametrize("outcome", ["refute", "inconclusive"])
def test_science_unchanged_by_delivery(
    managed: tuple[ManagedWork, Path, Path], outcome: str
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from epipilot.research.engine import ResearchEngine
    from epipilot.research.models import (
        Assessment,
        Cost,
        ExecutionStatus,
        ExperimentProposal,
        Observation,
        Outcome,
        ResearchGoal,
        ResearchNode,
        Scope,
    )

    service, root, _ = managed
    engine = ResearchEngine(service.store, service.project_id, root.parent)
    hid = uuid4()
    proposal = ExperimentProposal(
        id="science",
        hypothesis_id=hid,
        title="science",
        input_revision="frozen",
        estimated_cost=(Cost(unit="seconds", amount=1.0),),
        definitive_negative=True,
    )
    engine.initialize(
        ResearchGoal(statement="science", budgets=(Cost(unit="seconds", amount=10.0),)),
        (ResearchNode(hypothesis_id=hid, label="science", basis="user"),),
        (proposal,),
    )
    engine.reserve_next()
    engine.observe(
        Observation(
            proposal_id="science",
            execution=ExecutionStatus.SUCCEEDED,
            samples=20,
            actual_cost=(Cost(unit="seconds", amount=1.0),),
            artifact="synthetic",
            summary="science",
            recorded_at=datetime.now(UTC),
        )
    )

    class Verifier:
        def verify(self, proposal, observation):
            return Assessment(
                proposal_id="science",
                verified=True,
                outcome=Outcome(outcome),
                reliability=1.0,
                scope=Scope.INTEGRITY,
                reasons=("synthetic independent check",),
            )

    engine.resume(Verifier())
    before = engine.state
    lease = service.acquire("science-report", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("negative finding report\n")
    service.complete("science-report")
    service.publish("science-report", ("work:science-report",))
    after = replay_project(service.project_id, service.store.load(service.project_id))
    assert after.research == before.research
    assert after.hypotheses == before.hypotheses
    assert after.evidence == before.evidence
    assert before.hypotheses[0].status.value == (
        "refuted" if outcome == "refute" else "inconclusive"
    )


def test_forged_lifecycle_rejected(managed: tuple[ManagedWork, Path, Path]) -> None:
    from epipilot.core.events import EventType
    from epipilot.engineering.models import ManagedEvent
    from epipilot.research.engine import ResearchEngine

    service, root, _ = managed
    lease = service.acquire("forged", ("app.txt",))
    engine = ResearchEngine(service.store, service.project_id, root)
    forged = lease.model_copy(
        update={
            "status": WorkStatus.VERIFIED,
            "candidate": "a" * 40,
            "candidate_tree": "b" * 40,
            "task_commit": "c" * 40,
            "integration_base": lease.base,
            "profile_hash": "d" * 64,
        }
    )
    with pytest.raises(ValueError, match="transition"):
        engine.append(EventType.MANAGED_WORK, ManagedEvent(work=forged))


def test_publication_rejects_invented_references(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, _, _ = managed
    lease = service.acquire("refs", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("refs\n")
    service.complete("refs")
    with pytest.raises(ValueError, match="reference"):
        service.publish("refs", ("work:invented",))
    assert service.push_due()[0].operation_id == "refs"
    assert service.publish("refs", ("work:refs",)).status is WorkStatus.PUSHED
    assert not service.push_due()


def test_release_preserves_dirty_and_removes_only_owned(
    managed: tuple[ManagedWork, Path, Path],
) -> None:
    service, root, _ = managed
    lease = service.acquire("release", ("app.txt",))
    cwd = Path(lease.cwd)
    (cwd / "app.txt").write_text("release\n")
    result = service.complete("release")
    (cwd / "unsaved").write_text("keep")
    with pytest.raises(ValueError, match="dirty"):
        service.release("release")
    assert cwd.exists()
    (cwd / "unsaved").unlink()
    assert service.release("release").released
    assert not cwd.exists()
    assert service.release("release").released
    assert git(root, "rev-parse", "refs/heads/epipilot/integration") == result.integrated_commit


def test_scope_rejected(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, root, _ = managed
    lease = service.acquire("scope", ("app.txt",))
    (Path(lease.cwd) / "outside.txt").write_text("no\n")
    with pytest.raises(ValueError, match="scope"):
        service.complete("scope")
    assert git(root, "rev-parse", "refs/heads/epipilot/integration") == lease.base


def test_failed_gate_and_pollution(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, root, _ = managed
    policy = service.policy.model_copy(
        update={"acceptance": ((sys.executable, "-c", "raise SystemExit(1)"),)}
    )
    # Policy changes are explicit and possible only before a lease is allocated.
    fresh = ManagedWork(service.store, "failure", root)
    fresh.configure(
        policy.model_copy(
            update={
                "integration_branch": "epipilot/failure",
                "worktree_root": str(root.parent / "failed-work"),
            }
        )
    )
    lease = fresh.acquire("failure", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("new\n")
    with pytest.raises(ValueError, match="gate"):
        fresh.complete("failure")
    assert git(root, "rev-parse", "refs/heads/epipilot/failure") == lease.base


def test_cached_scope_cannot_hide_behind_clean_worktree(
    managed: tuple[ManagedWork, Path, Path],
) -> None:
    service, _, _ = managed
    lease = service.acquire("staged-scope", ("app.txt",))
    cwd = Path(lease.cwd)
    (cwd / "outside").write_text("hidden staged\n")
    git(cwd, "add", "outside")
    (cwd / "outside").unlink()
    (cwd / "app.txt").write_text("allowed\n")
    with pytest.raises(ValueError, match="scope"):
        service.complete("staged-scope")


def test_task_cannot_attach_to_user_branch(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, root, _ = managed
    lease = service.acquire("attached", ("app.txt",))
    git(Path(lease.cwd), "symbolic-ref", "HEAD", "refs/heads/main")
    before = git(root, "rev-parse", "HEAD")
    (Path(lease.cwd) / "app.txt").write_text("unsafe\n")
    with pytest.raises(ValueError, match="detached"):
        service.complete("attached")
    assert git(root, "rev-parse", "HEAD") == before


def test_base_drift_rebuilds_and_conflict_blocks(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, root, _ = managed
    first = service.acquire("first", ("app.txt",))
    second = service.acquire("second", ("app.txt",))
    (Path(first.cwd) / "app.txt").write_text("first\n")
    (Path(second.cwd) / "app.txt").write_text("second\n")
    integrated = service.complete("first")
    with pytest.raises(ValueError, match="conflict"):
        service.complete("second")
    assert git(root, "rev-parse", "refs/heads/epipilot/integration") == integrated.integrated_commit


def test_gate_pollution_blocks(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, root, _ = managed
    policy = service.policy.model_copy(
        update={
            "integration_branch": "epipilot/pollution",
            "worktree_root": str(root.parent / "pollution"),
            "acceptance": (
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('app.txt').write_text('polluted')",
                ),
            ),
        }
    )
    fresh = ManagedWork(service.store, "pollution", root)
    fresh.configure(policy)
    lease = fresh.acquire("pollution", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("candidate\n")
    with pytest.raises(ValueError, match="polluted"):
        fresh.complete("pollution")
    assert git(root, "rev-parse", "refs/heads/epipilot/pollution") == lease.base


def test_integration_result_loss_recovers_without_duplicate(
    managed: tuple[ManagedWork, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, root, _ = managed
    lease = service.acquire("recover", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("recovered\n")
    original = service.store.append

    def fail_result(event, *, expected_version=None):
        from epipilot.events.codec import decode_event_payload

        payload = decode_event_payload(event)
        if payload.work is not None and payload.work.status is WorkStatus.INTEGRATED:
            raise OSError("result lost")
        return original(event, expected_version=expected_version)

    monkeypatch.setattr(
        type(service.store), "append", lambda _store, event, **kw: fail_result(event, **kw)
    )
    with pytest.raises(OSError, match="lost"):
        service.complete("recover")
    integrated = git(root, "rev-parse", "refs/heads/epipilot/integration")
    monkeypatch.undo()
    recovered = ManagedWork(service.store, service.project_id, root).complete("recover")
    assert recovered.integrated_commit == integrated


def test_push_timeout_checks_remote_before_retry(
    managed: tuple[ManagedWork, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, root, remote = managed
    lease = service.acquire("timeout", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("timeout result\n")
    integrated = service.complete("timeout")
    original = service.git

    def timeout_after_push(cwd: Path, *args: str) -> str:
        result = original(cwd, *args)
        if args[0] == "push":
            raise subprocess.TimeoutExpired("git push", 60)
        return result

    monkeypatch.setattr(service, "git", timeout_after_push)
    assert service.publish("timeout", ("work:timeout",)).status is WorkStatus.PUSHED
    assert git(remote, "rev-parse", "refs/heads/delivery") == integrated.integrated_commit
    assert git(root, "rev-parse", "HEAD") == lease.base


def test_nonconflicting_drift_gets_current_base(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, _, _ = managed
    first = service.acquire("drift-first", ("one.txt",))
    second = service.acquire("drift-second", ("two.txt",))
    (Path(first.cwd) / "one.txt").write_text("one\n")
    (Path(second.cwd) / "two.txt").write_text("two\n")
    integrated = service.complete("drift-first")
    later = service.complete("drift-second")
    assert later.integration_base == integrated.integrated_commit
    assert later.candidate_tree == git(
        Path(second.cwd), "rev-parse", later.integrated_commit + "^{tree}"
    )


def test_cli_delivery(
    managed: tuple[ManagedWork, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from epipilot.research.agent_cli import main

    service, root, remote = managed
    flags = ["--root", str(root), "--db", str(service.store.path), "--project", service.project_id]
    assert main([*flags, "work", "acquire", "--operation", "cli", "--scope", "app.txt"]) == 0
    lease = json.loads(capsys.readouterr().out)
    (Path(lease["cwd"]) / "app.txt").write_text("cli delivered\n")
    assert main([*flags, "work", "complete", "--operation", "cli"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "integrated"
    assert main([*flags, "work", "publish", "--operation", "cli", "--artifact", "work:cli"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pushed"
    assert git(remote, "rev-parse", "refs/heads/delivery") == result["integrated_commit"]


def test_remote_descendant_is_idempotent_and_reported(
    managed: tuple[ManagedWork, Path, Path],
) -> None:
    service, root, remote = managed
    lease = service.acquire("descendant", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("descendant\n")
    integrated = service.complete("descendant")
    git(root, "push", "origin", integrated.integrated_commit + ":refs/heads/delivery")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    later = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "commit-tree",
            tree,
            "-p",
            integrated.integrated_commit,
            "-m",
            "later",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git(root, "push", "origin", later + ":refs/heads/delivery")
    result = service.publish("descendant", ("work:descendant",))
    assert result.status is WorkStatus.PUSHED
    assert "already-contained" in result.publication_result
    assert git(remote, "rev-parse", "refs/heads/delivery") != integrated.integrated_commit


def test_remote_divergence_never_forced(managed: tuple[ManagedWork, Path, Path]) -> None:
    service, root, remote = managed
    lease = service.acquire("push", ("app.txt",))
    (Path(lease.cwd) / "app.txt").write_text("candidate\n")
    service.complete("push")
    other_tree = git(root, "rev-parse", "HEAD^{tree}")
    unrelated = subprocess.run(
        ["git", "-C", str(root), "commit-tree", other_tree, "-m", "unrelated"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    git(root, "push", "origin", f"{unrelated}:refs/heads/delivery")
    with pytest.raises(ValueError, match="diverged"):
        service.publish("push", ("work:push",))
    assert git(remote, "rev-parse", "refs/heads/delivery") == unrelated
