from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

REQUIRED_FILES = (
    "world_spec.yaml",
    "world_spec.json",
    "manifest.json",
    "run_summary.json",
    "metrics.json",
    "realism_report.json",
    "failure_surface.json",
    "orders.parquet",
    "trades.parquet",
    "book_snapshots.parquet",
    "events.parquet",
    "agent_states.parquet",
    "report.md",
)

VALIDATION_REQUIRED_FILES = REQUIRED_FILES + (
    "calibration_pack.json",
    "intervention_results.json",
    "simulator_validation_report.json",
    "synthetic_release_validation_report.json",
    "synthetic_market_package_manifest.json",
    "latent_regimes.parquet",
    "intervention_labels.parquet",
)


def safe_artifact_dir(root: Path, experiment_id: str) -> Path:
    if not experiment_id.replace("-", "").isalnum():
        raise ValueError("experiment_id contains unsafe characters")
    root = root.resolve()
    destination = (root / experiment_id).resolve()
    if root not in destination.parents:
        raise ValueError("artifact path escapes configured root")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "figures").mkdir(exist_ok=True)
    return destination


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def write_parquet(path: Path, rows: list[dict]) -> None:
    frame = pd.json_normalize(rows) if rows else pd.DataFrame({"empty": pd.Series(dtype="bool")})
    frame.to_parquet(path, index=False)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
