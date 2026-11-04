"""Exercise real Docker egress separation with a zero-call, zero-money request policy."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from epipilot.benchfirst.isolation import Limits
from epipilot.benchfirst.live import GatewayDocker, OperatorApproval, docker
from epipilot.benchfirst.metering import Meter, RequestBudget


def qualify(image: str, output: Path, source: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    control, audit, work = (output / n for n in ("control", "audit", "work"))
    for path in (control, audit, work):
        path.mkdir()
    audit.chmod(0o700)
    work.chmod(0o777)
    capability = secrets.token_hex(32)
    budget = RequestBudget(
        cell_id="zero-call-network-probe",
        plan_sha256="0" * 64,
        model="gpt-5.3-codex",
        approval_ref="synthetic-negative-control-only",
        max_attempts=0,
        max_http_requests=0,
        max_input_tokens=8192,
        max_output_tokens=2048,
        input_nusd_per_token=1,
        output_nusd_per_token=1,
        max_cost_nusd=0,
        wall_seconds=180,
    )
    approval = OperatorApproval(
        operator_reference=budget.approval_ref,
        plan_sha256="0" * 64,
        approved_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=3),
        budget=budget,
    )
    (control / "approval.json").write_text(approval.model_dump_json())
    (control / "provider.key").write_text("synthetic-invalid-key-never-forwarded")
    (control / "capability").write_text(capability)
    for path in control.iterdir():
        path.chmod(0o444)
    harness = Path(__file__).resolve().parents[3] / "src"
    network, name = "epi-probe-" + uuid4().hex, "epi-proxy-" + uuid4().hex
    try:
        docker(
            "network", "create", "--internal", "--label", "epipilot.role=isolated-agent", network
        )
        docker(
            "create",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            "gateway",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=256m",
            "--memory-swap=256m",
            "--cpus=0.5",
            "--pids-limit=32",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=32m",
            "--mount",
            f"type=bind,src={harness},dst=/harness,readonly",
            "--mount",
            f"type=bind,src={control},dst=/control,readonly",
            "--mount",
            f"type=bind,src={audit},dst=/audit",
            "--env",
            "PYTHONPATH=/harness",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            image,
            "python",
            "-m",
            "epipilot.benchfirst.live",
            "gateway",
        )
        docker("network", "connect", "bridge", name)
        docker("start", name)
        for _ in range(30):
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "python",
                    "-c",
                    "import socket; socket.create_connection(('127.0.0.1',8080),1).close()",
                ],
                capture_output=True,
                timeout=3,
                check=False,
            )
            if probe.returncode == 0:
                break
            time.sleep(0.2)
        code = """
import json, os, socket, urllib.request, urllib.error
from pathlib import Path
assert not any(Path(p).exists() for p in ('/control','/audit','/private','/var/run/docker.sock'))
s = socket.socket(); s.settimeout(1)
assert s.connect_ex(('1.1.1.1',443)) != 0
statuses=[]
for token in ('wrong', os.environ['EPIPILOT_GATEWAY_TOKEN']):
    request=urllib.request.Request('http://gateway:8080/v1/responses',
        data=b'{"model":"gpt-5.3-codex","input":"synthetic negative probe"}',
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        urllib.request.urlopen(request,timeout=5)
        raise AssertionError('zero quota must deny')
    except urllib.error.HTTPError as e:
        statuses.append(e.code)
assert statuses == [403,409], statuses
print(json.dumps({'auth_statuses':statuses,'direct_egress_blocked':True,'private_mounts_absent':True}))
"""
        process = GatewayDocker(image, Limits(seconds=30, memory_mb=512), network).run(
            harness,
            source,
            work,
            ("python", "-c", code),
            output / "candidate-probe.log",
            environment={
                "PATH": os.environ["PATH"],
                "HOME": str(Path.home()),
                "EPIPILOT_GATEWAY_TOKEN": capability,
                "OPENAI_API_KEY": capability,
            },
        )
        summary = Meter(audit / "requests.sqlite3", budget).summary()
        (output / "results.json").write_text(
            json.dumps(
                {
                    "process": process.model_dump(mode="json"),
                    "requests": summary.model_dump(mode="json"),
                    "provider_key_was_synthetic": True,
                },
                indent=2,
            )
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, timeout=30, check=False
        )
        for path in (control / "provider.key", control / "capability"):
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    qualify(args.image, args.output, args.source)
