import json
import subprocess
import sys
from uuid import uuid4

from epipilot.research.models import (
    Cost,
    ExperimentProposal,
    ResearchDefinition,
    ResearchGoal,
    ResearchNode,
)


def test_generic_cli_dry_run_does_not_reserve_budget(tmp_path):
    hid = uuid4()
    definition = ResearchDefinition(
        goal=ResearchGoal(statement="synthetic", budgets=(Cost(unit="seconds", amount=10.0),)),
        nodes=(ResearchNode(hypothesis_id=hid, label="test", basis="synthetic"),),
        proposals=(
            ExperimentProposal(
                id="test",
                hypothesis_id=hid,
                title="test",
                input_revision="x",
                estimated_cost=(Cost(unit="seconds", amount=1.0),),
            ),
        ),
    )
    spec = tmp_path / "definition.json"
    spec.write_text(definition.model_dump_json(), encoding="utf8")
    common = [
        "--db",
        str(tmp_path / "events.sqlite3"),
        "--project",
        "generic",
        "--output",
        str(tmp_path / "out"),
    ]

    def command(name, *extra):
        result = subprocess.run(
            [sys.executable, "-m", "epipilot.research", name, *common, *extra],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    before = command("init", "--definition", str(spec))
    dry = command("dry-run")
    assert dry["reservation_preview"][0]["amount"] == 1.25
    after = command("show")
    assert before["event_version"] == after["event_version"]
    assert dry["mutated"] is False
