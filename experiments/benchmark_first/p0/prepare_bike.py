"""Prepare one real CPU calibration task, not an S1 research benchmark or victory test.

Reviewed source: UCI 275, CC BY 4.0, Fanaee-T (2013), doi:10.24432/C5W894.
Raw data and withheld labels stay outside the public repo/agent mount. No model calls.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

from epipilot.benchfirst.offline import digest
from epipilot.benchfirst.private_scoring import LabelSet
from epipilot.benchfirst.real_agents import RealTask

URL = "https://archive.ics.uci.edu/static/public/275/bike%2Bsharing%2Bdataset.zip"


def prepare(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    public = output / "public"
    private = output / "private"
    public.mkdir()
    private.mkdir(mode=0o700)
    with urllib.request.urlopen(URL, timeout=60) as response:
        raw = response.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("dataset download limit exceeded")
    (private / "source.zip").write_bytes(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        # Never extract archive paths or execute contents.
        info = archive.getinfo("hour.csv")
        if info.file_size > 2_097_152:
            raise ValueError("expanded dataset too large")
        rows = list(csv.DictReader(io.StringIO(archive.read(info).decode("utf-8"))))
    # Freeze chronology before calibration; exclude target components (direct leakage).
    features = [
        "season",
        "yr",
        "mnth",
        "hr",
        "holiday",
        "weekday",
        "workingday",
        "weathersit",
        "temp",
        "atemp",
        "hum",
        "windspeed",
    ]
    groups = {"train": [], "dev": [], "test": []}
    for row in rows:
        group = (
            "train"
            if row["dteday"] < "2012-07-01"
            else ("dev" if row["dteday"] < "2012-10-01" else "test")
        )
        groups[group].append(row)
    for group, selected in groups.items():
        columns = ["row_id", *features, *([] if group == "test" else ["target"])]
        with (public / f"{group}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(
                {
                    "row_id": row["instant"],
                    **{k: row[k] for k in features},
                    **({} if group == "test" else {"target": row["cnt"]}),
                }
                for row in selected
            )
    labels = LabelSet(
        row_ids=tuple(r["instant"] for r in groups["test"]),
        values=tuple(float(r["cnt"]) for r in groups["test"]),
    )
    (private / "labels.json").write_text(labels.model_dump_json())
    baseline = Path(__file__).with_name("tabular_candidate.py").read_text()
    (public / "candidate.py").write_text(baseline)
    (public / "task.md").write_text(
        "# Real CPU calibration: hourly bike demand\n\n"
        "Minimize development RMSE of target (hourly rentals). UCI Bike Sharing 275. "
        "Fanaee-T (2013), https://doi.org/10.24432/C5W894, CC BY 4.0.\n"
        "Train strictly on train.csv (dates before July 2012); dev.csv is July-September; "
        "test.csv contains October-December features only. Do not train on dev labels. "
        "No target-derived casual/registered counts are provided.\n"
        "Edit candidate.py. Running python candidate.py must write dev_predictions.csv and "
        "predictions.csv (row_id,prediction), preserving row order. "
        "Use input/*.csv in AIDE's native workspace, or *.csv at /work for Codex. "
        "Keep fitted artifacts self-contained; the frozen code must regenerate predictions. "
        "Maximum 3 development trials, 600 training CPU seconds, 2 CPUs, 4 GiB RAM.\n"
        "This familiar public task is for integration/calibration, not evidence of superiority.\n"
        "Use this exact starting baseline as a reference. Preserve the output interface.\n\n"
        "```python\n" + baseline + "\n```\n"
    )
    task = RealTask(
        task_id="uci-bike-hourly-calibration",
        objective="Minimize development RMSE",
        dataset_citation="Fanaee-T (2013), https://doi.org/10.24432/C5W894",
        data_license="CC-BY-4.0",
        public_files={p.name: digest(p) for p in sorted(public.iterdir())},
        training_seconds=600,
        steps=3,
    )
    (output / "task.json").write_text(task.model_dump_json(indent=2))
    (output / "preparation.json").write_text(
        json.dumps(
            {
                "source_url": URL,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "task_sha256": task.fingerprint,
                "rows": {k: len(v) for k, v in groups.items()},
                "model_calls": 0,
                "formal_s1_eligible": False,
                "reason": "calibration only; saturation not established",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output)
