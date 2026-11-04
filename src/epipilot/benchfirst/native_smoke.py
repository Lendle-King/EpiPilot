"""Container-only native framework smoke. Only model/client responses are scripted.

Upstream modules are deliberately dynamic: their public APIs are untyped and external.
No upstream source is copied or rewritten. Synthetic predictions are not research scores.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch


class ScriptedTransport:
    def __init__(self, crash: bool = False) -> None:
        self.attempts = 0
        self.trials = 0
        self.metric: float | None = None
        self.crash = crash
        self.history: list[dict[str, object]] = []

    def query(self, *args: object, **kwargs: Any) -> Any:
        if self.attempts >= 4:
            raise ValueError("mock model budget exhausted")
        self.attempts += 1
        review = kwargs.get("func_spec") is not None or kwargs.get("function_name") is not None
        self.history.append(
            {"kind": "mock_review" if review else "mock_code", "attempt": self.attempts}
        )
        if review:
            return {
                "is_bug": self.metric is None,
                "summary": "Synthetic offline feedback",
                "metric": self.metric,
                "lower_is_better": True,
            }
        slope = 1 if self.trials == 0 else 2
        code = (
            "import json\nfrom pathlib import Path\n"
            "task = json.loads(Path('task.json').read_text())\n"
            f"print(json.dumps([{slope} * x for x in task['x']]))\n"
        )
        if self.crash and self.trials == 0:
            code = "raise RuntimeError('intentional offline crash')\n"
        return "Synthetic fixed response for plumbing only.\n```python\n" + code + "```"

    def execute(self, code: str) -> subprocess.CompletedProcess[str]:
        if self.trials >= 3:
            raise ValueError("mock dev budget exhausted")
        self.trials += 1
        path = Path(f"trial-{self.trials}.py")
        path.write_text(code)
        result = subprocess.run(
            [sys.executable, "-I", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            values = json.loads(result.stdout)
            task = json.loads(Path("task.json").read_text())
            self.metric = sum(
                (p - y) ** 2 for p, y in zip(values, task["dev_y"], strict=True)
            ) / len(values)
        else:
            self.metric = None
        self.history.append(
            {
                "kind": "synthetic_trial",
                "trial": self.trials,
                "exit_code": result.returncode,
                "dev_mse": self.metric,
            }
        )
        return result


def aide_smoke(mock: ScriptedTransport) -> str:
    agent_module = importlib.import_module("aide.agent")
    journal_module = importlib.import_module("aide.journal")
    execution = importlib.import_module("aide.interpreter")
    omega = importlib.import_module("omegaconf").OmegaConf
    cfg = omega.load("/framework/aide/utils/config.yaml")
    cfg.workspace_dir = "/work"
    cfg.agent.search.num_drafts = 1
    cfg.agent.search.debug_prob = 1.0
    cfg.agent.metric_maximize = False
    journal = journal_module.Journal(metric_maximize=False)
    agent = agent_module.Agent("Synthetic public task: predict 2*x; minimize MSE", cfg, journal)

    def execute(code: str, reset: bool) -> Any:
        result = mock.execute(code)
        return execution.ExecutionResult(
            term_out=[result.stdout, result.stderr],
            exec_time=0.0,
            exc_type=None if result.returncode == 0 else "RuntimeError",
        )

    with patch.object(agent_module, "query", mock.query):
        for _ in range(2):
            agent.step(exec_callback=execute)
    best = journal.get_best_node()
    if best is None or len(journal.nodes) != 2 or best.parent is None:
        raise ValueError("AIDE did not preserve draft/debug-or-improve tree")
    serialize = importlib.import_module("aide.utils.serialize")
    serialize.dump_json(journal, Path("native-journal.json"))
    restored = serialize.load_json(Path("native-journal.json"), journal_module.Journal)
    if restored.get_best_node().code != best.code:
        raise ValueError("AIDE journal recovery changed best candidate")
    return str(best.code)


def aira_smoke(mock: ScriptedTransport) -> str:
    # Match upstream registry initialization order; direct Greedy import is circular.
    importlib.import_module("dojo.config_dataclasses.solver")
    greedy_module = importlib.import_module("dojo.solvers.greedy.greedy")
    generic = importlib.import_module("dojo.core.solvers.llm_helpers.generic_llm")
    omega = importlib.import_module("omegaconf").OmegaConf
    constants = importlib.import_module("dojo.core.tasks.constants")
    execution = importlib.import_module("dojo.core.interpreters.base")
    cfg = omega.load("/framework/src/dojo/configs/solver/greedy.yaml")
    del cfg.defaults
    cfg.num_drafts, cfg.debug_prob, cfg.max_debug_depth = 1, 1.0, 3
    cfg.checkpoint_path = "/work/checkpoint"
    cfg.memory = {"memory_processor": "simple_memory", "memory_op_kwargs": {}}
    cfg.debug_memory = {"memory_processor": "ancestral_memory", "memory_op_kwargs": {}}
    operators = {}
    for name in ("draft", "improve", "debug", "analyze"):
        group = "aide_operators" if name == "analyze" else "aira_operators"
        operator = omega.load(
            f"/framework/src/dojo/configs/solver/operators/mlebench/{group}/{name}.yaml"
        )[name]
        operator.llm.client = {"api": "offline-mock"}
        # Upstream dataclass defaults for optional user-message templates.
        empty = {
            "_target_": "dojo.config_dataclasses.llm.jinjaprompt.JinjaPromptConfig",
            "template": "",
            "input_variables": [],
        }
        operator.init_user_message_prompt_template = empty
        operator.user_message_prompt_template = empty
        operators[name] = operator
    cfg.operators = operators
    importlib.import_module("dojo.utils.logger").get_logger().config(None)

    class Client:
        client_content_key = "content"

        def query(self, *args: object, **kwargs: Any) -> tuple[Any, dict[str, int]]:
            return mock.query(*args, **kwargs), {"input_tokens": 0, "output_tokens": 0}

    class Task:
        def step_task(self, state: Any, code: str) -> tuple[Any, dict[str, Any]]:
            result = mock.execute(code)
            return state, {
                constants.EXECUTION_OUTPUT: execution.ExecutionResult(
                    term_out=[result.stdout, result.stderr],
                    exec_time=0.0,
                    exit_code=result.returncode,
                ),
                constants.VALIDATION_FITNESS: mock.metric,
            }

    class PreviewInterpreter:
        local = True
        working_dir = Path("/work")

    # Replace provider boundary only. Native prompt operators, memory, step, journal,
    # parse_eval_result, search_policy and checkpoint implementation remain original.
    with patch.object(generic, "get_client", lambda _: Client()):
        solver = greedy_module.Greedy(
            cfg, {constants.TASK_DESCRIPTION: "Synthetic predict 2*x", "lower_is_better": True}
        )
        solver.create_root_node()
        state = {"solver_interpreter": PreviewInterpreter()}
        for _ in range(2):
            solver.step(Task(), state)
            solver.state.current_step += 1
            solver.save_checkpoint()
        best = solver.journal.get_best_node()
        if best is None or len(solver.journal.nodes) != 3 or not best.parents:
            raise ValueError("AIRA Greedy journal/parent relationship missing")
        solver.load_checkpoint()
        return str(best.code)


def epipilot_smoke(mock: ScriptedTransport) -> str:
    models = importlib.import_module("epipilot.research.models")
    planner = importlib.import_module("epipilot.research.planner")
    study = Path("/work/study")
    study.mkdir()
    store = importlib.import_module("epipilot.research.store").ResearchStore(study / ".research")
    store.add("goal_set", models.ResearchGoal(statement="S0 synthetic development only", budget=3))
    store.add(
        "unknown_added", models.Unknown(id="U", question="Which synthetic slope reduces MSE?")
    )
    for index in range(2):
        store.add(
            "proposal_added",
            models.Proposal(
                id=f"P{index}",
                title=f"Synthetic candidate {index}",
                kind=models.ProposalKind.EXPLORE,
                rationale="Offline control",
                acceptance="Independent MSE recomputation",
                unknown_ids=["U"],
                information_gain=0.8 - index * 0.1,
                expected_improvement=0.5,
                estimated_cost=1,
                risk=0.1,
            ),
        )
    (study / "task.json").write_bytes(Path("task.json").read_bytes())
    (study / ".gitignore").write_text(".research/\nresults/\n")
    # Substantive verifier is frozen in the study before observing results.
    verifier = (
        "import json, math\nfrom pathlib import Path\n"
        "task=json.loads(Path('task.json').read_text())\n"
        "pred=json.loads(Path('results/predictions.json').read_text())\n"
        "reported=json.loads(Path('results/metrics.json').read_text())['metrics']['mse']\n"
        "mse=sum((a-b)**2 for a,b in zip(pred,task['dev_y'],strict=True))/len(pred)\n"
        "assert math.isfinite(mse) and abs(mse-reported)<1e-12\n"
    )
    (study / "verify_dev.py").write_text(verifier)
    for argv in (
        ("init", "-q"),
        ("config", "user.name", "S0 Synthetic"),
        ("config", "user.email", "synthetic@example.invalid"),
        ("config", "commit.gpgsign", "false"),
        ("config", "core.hooksPath", "/dev/null"),
    ):
        subprocess.run(["git", "-C", str(study), *argv], capture_output=True, check=True)
    cli = importlib.import_module("epipilot.research.cli").main
    rankings = []
    code = ""
    for index in range(2):
        state, _ = store.load()
        ranked = planner.rank_proposals(state)
        selected = ranked[0]
        rankings.append({"proposal_id": selected.proposal.id, "rationale": selected.rationale})
        response = str(mock.query())
        code = response.split("```python\n")[1].split("```")[0]
        (study / "candidate.py").write_text(code)
        subprocess.run(
            [
                "git",
                "-C",
                str(study),
                "add",
                "--",
                ".gitignore",
                "task.json",
                "candidate.py",
                "verify_dev.py",
            ],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(study), "commit", "-qm", f"Synthetic candidate {index}"],
            capture_output=True,
            check=True,
        )

        def command(*args: str) -> None:
            if cli(["--root", str(study), *args]) != 0:
                raise ValueError("native EpiPilot lifecycle rejected synthetic study")

        command(
            "preregister",
            "--proposal",
            selected.proposal.id,
            "--baseline",
            "synthetic zero",
            "--config",
            "candidate.py",
            "--dataset",
            "task.json",
            "--control",
            "same public dev",
            "--decision-rule",
            "synthetic MSE is independently recomputed",
            "--falsification",
            "mismatching predictions reject this plumbing check",
            "--metric-rule",
            "mse:at_most:10",
            "--artifact",
            "results/metrics.json",
            "--max-cost",
            "1",
            "--verifier",
            sys.executable,
            "verify_dev.py",
            "{artifact}",
        )
        state, _ = store.load()
        contract = next(
            c for c in state.contracts.values() if c.proposal_id == selected.proposal.id
        )
        command("start", "--contract", contract.id)
        result = mock.execute(code)
        if result.returncode:
            command(
                "abort",
                "--contract",
                contract.id,
                "--reason",
                "intentional synthetic crash",
                "--cost",
                "0.01",
            )
            continue
        results = study / "results"
        results.mkdir(exist_ok=True)
        (results / "predictions.json").write_text(result.stdout)
        (results / "metrics.json").write_text(json.dumps({"metrics": {"mse": mock.metric}}))
        command(
            "record",
            "--contract",
            contract.id,
            "--summary",
            "S0 synthetic only",
            "--outcome",
            "inconclusive",
            "--cost",
            "0.01",
            "--metric",
            f"mse={mock.metric}",
        )
        state, _ = store.load()
        exp = next(e for e in state.experiments.values() if e.contract_id == contract.id)
        command("verify", "--experiment", exp.id)
        state, _ = store.load()
        if not state.evidence[exp.evidence_id].measurement_validated:
            raise ValueError("native EpiPilot evidence gate did not validate")
    Path("native-ranking.json").write_text(json.dumps(rankings))
    return code


def agent_only_smoke(mock: ScriptedTransport) -> str:
    code = ""
    for _ in range(2):
        response = str(mock.query())
        code = response.split("```python\n")[1].split("```")[0]
        mock.execute(code)
        mock.query(func_spec=True)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family",
        choices=["agent_only", "aide_ml", "aira_dojo", "epipilot_current"],
        required=True,
    )
    parser.add_argument("--inject-crash", action="store_true")
    args = parser.parse_args()
    if os.environ.get("EPIPILOT_S0_CONTAINER") != "1":
        raise ValueError("native code must run through OfflineDocker")
    if args.family == "epipilot_current":
        sys.path.insert(0, "/framework/src")
    random.seed(17)
    mock = ScriptedTransport(args.inject_crash)
    start = time.monotonic()
    drivers = {
        "agent_only": agent_only_smoke,
        "aide_ml": aide_smoke,
        "aira_dojo": aira_smoke,
        "epipilot_current": epipilot_smoke,
    }
    try:
        code = drivers[args.family](mock)
        candidate = Path("candidate.py")
        candidate.write_text(code)
        result = subprocess.run(
            [sys.executable, "-I", str(candidate)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        task = json.loads(Path("task.json").read_text())
        Path("prediction.json").write_text(
            json.dumps(
                {
                    "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "row_ids": task["row_ids"],
                    "values": json.loads(result.stdout),
                }
            )
        )
    finally:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        Path("native-diagnostics.json").write_text(
            json.dumps(
                {
                    "family": args.family,
                    "real_model_attempts": 0,
                    "mock_model_attempts": mock.attempts,
                    "synthetic_dev_trials": mock.trials,
                    "wall_seconds": time.monotonic() - start,
                    "cpu_seconds": usage.ru_utime
                    + usage.ru_stime
                    + children.ru_utime
                    + children.ru_stime,
                    "peak_self_rss_kb": usage.ru_maxrss,
                    "gpu_seconds": 0,
                    "history": mock.history,
                    "evidence_gate_smoked": args.family == "epipilot_current" and mock.trials == 2,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
