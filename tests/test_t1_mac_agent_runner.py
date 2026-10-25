"""Mac T1 dispatcher fail-closed checks; never requires Codex or paid inference in CI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.researchbench_t1 import mac_agent_runner as mac


def test_model_selection_rejects_shell_or_unrecognized_chars() -> None:
    assert mac._model_name("gpt-6-luna(max)") == "gpt-6-luna(max)"
    assert mac._model_name("default") == "default"
    for model in ("", "$(id)", "gpt-6-luna; rm -rf /", "\nmodel", " "):
        with pytest.raises(mac.MacRunBlocked):
            mac._model_name(model)


def test_diagnose_is_no_inference_and_reports_unverified_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mac.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mac.shutil, "which", lambda binary: None)
    report = mac.run("diagnose", "ccswitch_codex", "gpt-6-luna(max)", "", Path("/unused"))
    assert report["model_calls"] == 0
    assert report["model_usable"] == "not_verified"
    assert report["readiness"] == "codex_missing"


def test_one_cell_requires_explicit_authorization_before_any_agent_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden() -> str:
        raise AssertionError("Codex should never be contacted before explicit opt-in")

    monkeypatch.setattr(mac, "_codex", forbidden)
    with pytest.raises(mac.MacRunBlocked, match="RUN_ONE_CELL"):
        mac.one_cell(tmp_path, "codex_chatgpt", "default", "")


def test_offline_smoke_uses_only_local_t1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands = []

    def fake_t1(*args: str, timeout: int = 0) -> dict[str, object]:
        commands.append(args[0])
        assert timeout <= 180
        return {
            "final": {"holdout_auroc": {"offline_baseline__wdbc__42": 0.8}},
            "trials": [{}, {}],
            "status": {"reserved_or_spent_seconds": 4.0},
        }

    monkeypatch.setattr(mac, "_t1", fake_t1)
    monkeypatch.setattr(mac, "_codex", lambda: (_ for _ in ()).throw(AssertionError))
    result = mac.offline_smoke(tmp_path)
    assert commands == ["smoke"]
    assert result["model_calls"] == 0
    assert result["training_attempts"] == 2


def test_one_cell_never_executes_model_proposed_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_calls = []
    stages = []

    monkeypatch.setattr(mac, "_codex", lambda: "/opt/homebrew/bin/codex")
    monkeypatch.setattr(mac, "_check_chatgpt_login", lambda binary: True)

    def fake_t1(*args: str, timeout: int = 0) -> dict[str, object]:
        stages.append(args[0])
        if args[0] == "trial":
            return {"exit_code": 0, "charged_seconds": 2.0}
        if args[0] == "submit":
            return {"id": "selected"}
        return {}

    def fake_run(argv: list[str], **kwargs: object) -> str:
        if "task" in argv:
            return "Optimize held-out-free development AUROC, return only candidate JSON"
        return ""

    def fake_codex(argv: list[str], **kwargs: object) -> SimpleNamespace:
        model_calls.append((argv, kwargs))
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text(
            '{"model":"logistic","feature_mode":"all","C":1.0,"class_weight":"none"}',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mac, "_t1", fake_t1)
    monkeypatch.setattr(mac, "_run", fake_run)
    monkeypatch.setattr(mac.subprocess, "run", fake_codex)
    result = mac.one_cell(tmp_path, "codex_chatgpt", "gpt-6-luna(max)", "RUN_ONE_CELL")
    assert stages == ["init", "trial", "submit"]
    assert len(model_calls) == 1
    argv, kwargs = model_calls[0]
    assert argv[0].endswith("codex")
    assert argv[1:4] == ["exec", "--sandbox", "read-only"]
    assert argv[argv.index("--model") + 1] == "gpt-6-luna(max)"
    assert kwargs["timeout"] == mac.MODEL_TIMEOUT
    assert "shell" not in kwargs
    assert result["model_calls"] == 1
    assert result["trial_completed"] is True


def test_bad_model_reply_cannot_trigger_trial_or_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    monkeypatch.setattr(mac, "_codex", lambda: "codex")
    monkeypatch.setattr(mac, "_check_chatgpt_login", lambda binary: True)

    def fake_t1(*args: str, timeout: int = 0) -> dict[str, object]:
        calls.append(args[0])
        return {}

    def fake_run(argv: list[str], **kwargs: object) -> str:
        return "propose one candidate"

    def fake_codex(argv: list[str], **kwargs: object) -> SimpleNamespace:
        dest = Path(argv[argv.index("--output-last-message") + 1])
        dest.write_text('{"model":"exec","command":"touch /tmp/unsafe"}', encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mac, "_t1", fake_t1)
    monkeypatch.setattr(mac, "_run", fake_run)
    monkeypatch.setattr(mac.subprocess, "run", fake_codex)
    with pytest.raises(mac.MacRunBlocked, match="valid frozen T1 candidate"):
        mac.one_cell(tmp_path, "codex_chatgpt", "default", "RUN_ONE_CELL")
    assert calls == ["init"]


def test_denied_mode_records_sanitized_receipt_without_paid_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mac.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mac, "_codex", lambda: (_ for _ in ()).throw(AssertionError))
    output = tmp_path / "receipt"
    assert (
        mac.main(
            [
                "--mode", "pilot_one_cell", "--provider", "codex_chatgpt",
                "--model", "gpt-6-luna(max)", "--results", str(output),
            ]
        )
        == 2
    )
    data = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert data["status"] == "blocked"
    assert data["model_calls_confirmed"] == 0
    assert "RUN_ONE_CELL" in data["safe_reason"]


def test_environment_scrubs_github_and_ssh_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "not-to-be-inherited")
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "not-to-be-inherited")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/key-agent")
    environment = mac._redacted_agent_env()
    assert "GITHUB_TOKEN" not in environment
    assert "ACTIONS_RUNTIME_TOKEN" not in environment
    assert "SSH_AUTH_SOCK" not in environment
    assert environment["CI"] == "1"
