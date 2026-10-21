"""Isolated bridge to the QUANT_ETF official SSE announcement archive runner."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured

ADAPTER_ID = "quant_etf_sse_announcement_archive_v1"
CODE_PATHS = ("src/quant_etf/sse_announcement_archive.py",)
MAX_RUN_SECONDS = 630

_RUNNER = r"""
import json
import sys
import tempfile
from pathlib import Path
from uuid import UUID

from epipilot.research.frontier import MeasurementContract
from epipilot.research.measurement import Measured
from epipilot.research.models import Cost, ExperimentKind, ExperimentProposal, Scope
from quant_etf.sse_announcement_archive import (
    SseAnnouncementArchiveRunner,
    SseAnnouncementArchiveVerifier,
)

contract = MeasurementContract.model_validate_json(sys.stdin.read())
proposal = ExperimentProposal(
    id=contract.proposal_id,
    hypothesis_id=UUID(int=0),
    title="Bounded official SSE announcement archive measurement",
    input_revision=contract.fingerprint(),
    kind=ExperimentKind.DATA_QUALIFICATION,
    scope=Scope.INTEGRITY,
    estimated_cost=(Cost(unit="wall_seconds", amount=630.0),),
)
with tempfile.TemporaryDirectory(prefix="epipilot-sse-archive-") as temporary:
    artifact_path = Path(temporary) / "capture.json"
    runner = SseAnnouncementArchiveRunner(Path.cwd(), contract)
    observation = runner.run(proposal, artifact_path)
    assessment = SseAnnouncementArchiveVerifier(Path.cwd(), contract).verify(proposal, observation)
    if not assessment.verified:
        raise ValueError("SSE archive response failed independent artifact verification")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    pages = artifact.get("pages", [])
    stable_capture = {
        "query_plan_sha256": artifact.get("query_plan_sha256"),
        "pages_complete": artifact.get("pages_complete"),
        "declared_page_count": artifact.get("declared_page_count"),
        "transport_error": bool(artifact.get("transport_error")),
        "parse_error": bool(artifact.get("parse_error")),
        "pages": [
            {
                key: page.get(key)
                for key in (
                    "page",
                    "params",
                    "url",
                    "status",
                    "response_sha256",
                    "response_base64",
                    "row_count",
                )
            }
            for page in pages
        ],
    }
    measured = Measured(
        metrics=observation.metrics,
        samples=observation.samples,
        details=(json.dumps(stable_capture, ensure_ascii=False, sort_keys=True),),
    )
    sys.stdout.write(measured.model_dump_json())
"""


class SubprocessSseAnnouncementArchiveComputer(Computer):
    """Run and independently verify a bounded official SSE archive query."""

    quality = Quality.MEASUREMENT

    def __init__(self, python_executable: str | Path, epipilot_source: Path):
        self.python_executable = Path(python_executable).absolute()
        self.epipilot_source = epipilot_source.resolve()

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID:
            raise ValueError("contract uses another SSE announcement adapter")
        repository = root.resolve()
        code_check = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "diff",
                "--quiet",
                contract.git_revision,
                "--",
                *CODE_PATHS,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if code_check.returncode != 0:
            raise ValueError("SSE announcement adapter code differs from its pinned revision")

        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}
        }
        env["PYTHONPATH"] = os.pathsep.join((str(repository / "src"), str(self.epipilot_source)))
        process = subprocess.run(
            [str(self.python_executable), "-c", _RUNNER],
            input=contract.model_dump_json(),
            cwd=repository,
            env=env,
            text=True,
            capture_output=True,
            timeout=MAX_RUN_SECONDS,
            check=False,
        )
        if process.returncode != 0:
            raise ValueError(f"SSE announcement archive process failed (exit {process.returncode})")
        try:
            return Measured.model_validate_json(process.stdout)
        except ValueError:
            raise ValueError("SSE announcement archive returned invalid measurement JSON") from None
