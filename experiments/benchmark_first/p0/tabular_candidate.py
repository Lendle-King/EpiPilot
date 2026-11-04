"""Frozen calibration baseline; real task data remain outside Git."""

import csv
import json
import os
from pathlib import Path

os.environ["OMP_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

data = Path("input") if (Path("input") / "train.csv").exists() else Path()


def read(name: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    with (data / name).open() as handle:
        rows = list(csv.DictReader(handle))
    features = [k for k in rows[0] if k not in {"row_id", "target"}]
    return (
        [r["row_id"] for r in rows],
        np.array([[float(r[k]) for k in features] for r in rows]),
        np.array([float(r["target"]) for r in rows]) if "target" in rows[0] else np.array([]),
    )


_, train_x, train_y = read("train.csv")
model = (
    HistGradientBoostingRegressor(max_iter=200, random_state=0)
    if os.environ.get("CALIBRATION_REFERENCE") == "1"
    else make_pipeline(StandardScaler(), Ridge(alpha=1.0))
)
model.fit(train_x, train_y)
for split, output in (("dev", "dev_predictions.csv"), ("test", "predictions.csv")):
    ids, x, y = read(split + ".csv")
    predictions = model.predict(x)
    with Path(output).open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("row_id", "prediction"))
        writer.writerows(zip(ids, predictions, strict=True))
    if len(y):
        print(json.dumps({"development_rmse": float(np.sqrt(np.mean((predictions - y) ** 2)))}))
