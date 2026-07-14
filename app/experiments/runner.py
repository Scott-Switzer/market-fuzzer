from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analytics import build_failure_surface, build_realism_report
from app.schemas import WorldSpec
from app.simulation import SimulationResult, run_simulation
from app.world import SCENARIOS, mutate_scenario

from .artifacts import REQUIRED_FILES, safe_artifact_dir, sha256, write_json, write_parquet


@dataclass
class ExperimentResult:
    experiment_id: str
    artifact_dir: Path
    runs: list[dict]
    realism_report: dict
    failure_surface: dict
    manifest: dict

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "artifact_dir": str(self.artifact_dir),
            "runs": self.runs,
            "realism_report": self.realism_report,
            "failure_surface": self.failure_surface,
            "manifest": self.manifest,
        }


def run_single(spec: WorldSpec) -> SimulationResult:
    return run_simulation(spec)


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unavailable"


def run_batch(
    base: WorldSpec, artifact_root: str | Path | None = None, quick: bool = True
) -> ExperimentResult:
    root_value = artifact_root if artifact_root is not None else os.getenv("SMW_ARTIFACT_DIR", "artifacts")
    root = Path(root_value)
    experiment_id = f"smw-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{base.specification_hash()[:8]}"
    destination = safe_artifact_dir(root, experiment_id)
    participations = [0.02, 0.08, 0.20]
    seeds = (
        [base.seed, base.seed + 1]
        if quick
        else [base.seed + index for index in range(base.experiment.repetitions)]
    )
    run_rows: list[dict] = []
    results: list[SimulationResult] = []
    mutations: list[dict] = []
    for scenario in SCENARIOS:
        scenario_spec, mutation = mutate_scenario(base, scenario)
        mutations.append(mutation)
        for participation in participations:
            for seed in seeds:
                data = scenario_spec.model_dump()
                data["seed"] = seed
                data["experiment"]["participation_rate"] = participation
                data["experiment"]["strategy"] = "pov"
                spec = WorldSpec.model_validate(data)
                result = run_simulation(spec)
                results.append(result)
                run_rows.append(
                    {
                        "run_id": len(run_rows) + 1,
                        "scenario": scenario,
                        "participation_rate": participation,
                        "seed": seed,
                        "spec_hash": result.spec_hash,
                        "result_hash": result.result_hash,
                        "runtime_ms": result.runtime_ms,
                        "metrics": result.summary,
                    }
                )
    realism = build_realism_report(results[0], base.experiment.target_asset)
    failure = build_failure_surface(run_rows)
    orders, trades, snapshots, events, states = [], [], [], [], []
    for row, result in zip(run_rows, results, strict=True):
        identity = {
            "run_id": row["run_id"],
            "scenario": row["scenario"],
            "seed": row["seed"],
            "participation_rate": row["participation_rate"],
        }
        orders.extend([{**identity, **item} for item in result.orders])
        trades.extend([{**identity, **item} for item in result.trades])
        events.extend([{**identity, **item} for item in result.events])
        states.extend(
            [
                {**identity, **item, "inventory": json.dumps(item["inventory"], sort_keys=True)}
                for item in result.agent_states
            ]
        )
        for frame in result.timeline:
            for symbol, state in frame["asset_states"].items():
                snapshots.append(
                    {
                        **identity,
                        "step": frame["step"],
                        "symbol": symbol,
                        **{key: value for key, value in state.items() if key != "book"},
                        "book": json.dumps(state["book"], sort_keys=True),
                    }
                )
    (destination / "world_spec.yaml").write_text(base.to_yaml())
    write_json(destination / "world_spec.json", base.model_dump(mode="json"))
    write_json(destination / "run_summary.json", run_rows)
    write_json(destination / "metrics.json", {"runs": run_rows})
    write_json(destination / "realism_report.json", realism)
    write_json(destination / "failure_surface.json", failure)
    write_parquet(destination / "orders.parquet", orders)
    write_parquet(destination / "trades.parquet", trades)
    write_parquet(destination / "book_snapshots.parquet", snapshots)
    write_parquet(destination / "events.parquet", events)
    write_parquet(destination / "agent_states.parquet", states)
    report = [
        "# Synthetic Market World experiment",
        "",
        f"World hash: `{base.specification_hash()}`",
        "",
        f"Runs: {len(run_rows)} across {len(SCENARIOS)} scenarios, {len(participations)} participation rates, and {len(seeds)} common seeds.",
        "",
        "## Evidence-bounded result",
        "",
        f"Worst observed aggregate cell: {failure['worst']}",
        "",
        "## Limitations",
        "",
        realism["disclaimer"],
        "",
    ]
    (destination / "report.md").write_text("\n".join(report))
    manifest: dict[str, Any] = {
        "experiment_id": experiment_id,
        "created_at": datetime.now(UTC).isoformat(),
        "code_commit": _commit(),
        "world_hash": base.specification_hash(),
        "seeds": seeds,
        "engine_backend": "internal_exact_clob",
        "python": platform.python_version(),
        "scenario_mutations": mutations,
        "strategy_parameters": base.experiment.model_dump(mode="json"),
        "compiler_mode": "offline",
        "openai_model": None,
        "reproduction_command": f"smw batch configs/presets/fragile-small-cap.yaml --seed {base.seed}",
        "artifact_hashes": {},
    }
    for filename in REQUIRED_FILES:
        if filename != "manifest.json":
            manifest["artifact_hashes"][filename] = sha256(destination / filename)
    write_json(destination / "manifest.json", manifest)
    return ExperimentResult(experiment_id, destination, run_rows, realism, failure, manifest)
