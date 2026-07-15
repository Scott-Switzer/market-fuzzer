from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analytics import build_failure_surface, build_realism_report
from app.analytics.claims import evaluate_participation_claim
from app.calibration import CalibrationPackV1, build_demo_calibration_pack, calibrate_bootstrap
from app.schemas import WorldSpec
from app.simulation import SimulationResult, run_simulation
from app.validation import build_release_validation_report, build_simulator_validation_report
from app.world import SCENARIOS, mutate_scenario

from .artifacts import (
    REQUIRED_FILES,
    VALIDATION_REQUIRED_FILES,
    safe_artifact_dir,
    sha256,
    write_json,
    write_parquet,
)


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


@dataclass
class ValidationCampaignResult:
    experiment_id: str
    artifact_dir: Path
    calibration_pack: dict
    intervention_summary: dict
    simulator_validation_report: dict
    synthetic_release_validation_report: dict
    manifest: dict

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "artifact_dir": str(self.artifact_dir),
            "calibration_pack": self.calibration_pack,
            "intervention_summary": self.intervention_summary,
            "simulator_validation_report": self.simulator_validation_report,
            "synthetic_release_validation_report": self.synthetic_release_validation_report,
            "manifest": self.manifest,
        }


def run_single(spec: WorldSpec) -> SimulationResult:
    return run_simulation(spec)


def _campaign_spec(
    base: WorldSpec,
    pack_id: str,
    parameter_set_id: str,
    parameters: dict[str, float],
    participation: float,
    seed: int,
    quick: bool,
) -> WorldSpec:
    data = base.model_dump(mode="python")
    data["schema_version"] = "1.1"
    data["world_id"] = f"{base.world_id}-calibrated-{parameter_set_id[-6:]}-{int(participation * 100):02d}"
    data["seed"] = seed
    data["world_type"] = "emergent_calibrated"
    data["calibration_pack_id"] = pack_id
    data["calibration_parameter_set_id"] = parameter_set_id
    data["order_flow_provider"] = "queue_reactive"
    limit_intensity = max(0.1, parameters["limit_intensity"])
    data["order_flow_parameters"] = {
        "bid_limit_intensity": limit_intensity,
        "ask_limit_intensity": limit_intensity,
        "bid_cancel_intensity": 0.35,
        "ask_cancel_intensity": 0.35,
        "buy_market_intensity": 0.30,
        "sell_market_intensity": 0.30,
        "base_order_size": min(300.0, max(20.0, parameters["base_order_size"])),
        "flow_persistence": parameters["flow_persistence"],
        "volatility_sensitivity": parameters["volatility_sensitivity"],
    }
    data["interventions"] = {
        "participation_rate": participation,
        "displayed_depth_multiplier": 0.5,
        "forced_seller_quantity": 3_000,
        "labels": ["50_percent_depth_reduction", "forced_seller", "participation_sweep"],
    }
    data["experiment"]["participation_rate"] = participation
    data["experiment"]["strategy"] = "pov"
    if quick:
        start = data["clock"]["start"]
        data["clock"]["end"] = start + (base.clock.end - base.clock.start) * (16 / base.clock.steps)
        data["events"] = []
    data["ground_truth_labels"] = {
        "calibration_ensemble": True,
        "depth_multiplier": 0.5,
        "forced_seller_quantity": 3_000,
        "response": "emergent_observation",
    }
    return WorldSpec.model_validate(data)


def run_validation_campaign(
    base: WorldSpec,
    artifact_root: str | Path | None = None,
    mode: str = "quick",
    calibration_pack: CalibrationPackV1 | None = None,
) -> ValidationCampaignResult:
    if mode not in {"quick", "audit"}:
        raise ValueError("mode must be quick or audit")
    quick = mode == "quick"
    pack = calibration_pack or build_demo_calibration_pack()
    calibration = calibrate_bootstrap(pack, mode="quick" if quick else "audit", seed=base.seed + 701)
    sets = calibration.accepted_parameter_sets[:3] if quick else calibration.accepted_parameter_sets[:10]
    seeds = [base.seed + index for index in range(8 if quick else 30)]
    participations = [0.02, 0.05, 0.10, 0.20]
    root_value = artifact_root if artifact_root is not None else os.getenv("SMW_ARTIFACT_DIR", "artifacts")
    experiment_id = f"cmv-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{base.specification_hash()[:8]}"
    destination = safe_artifact_dir(Path(root_value), experiment_id)
    rows: list[dict] = []
    results: list[SimulationResult] = []
    specs: list[WorldSpec] = []
    for parameter_set in sets:
        for seed in seeds:
            for participation in participations:
                spec = _campaign_spec(
                    base,
                    pack.pack_id,
                    parameter_set.parameter_set_id,
                    parameter_set.parameters,
                    participation,
                    seed,
                    quick,
                )
                result = run_simulation(spec)
                specs.append(spec)
                results.append(result)
                rows.append(
                    {
                        "run_id": len(rows) + 1,
                        "scenario": "depth_50pct_plus_forced_seller",
                        "calibration_parameter_set_id": parameter_set.parameter_set_id,
                        "seed": seed,
                        "participation_rate": participation,
                        "displayed_depth_multiplier": 0.5,
                        "forced_seller_quantity": 3_000,
                        "spec_hash": result.spec_hash,
                        "result_hash": result.result_hash,
                        "implementation_shortfall_bps": result.summary["implementation_shortfall_bps"],
                        "metrics": result.summary,
                    }
                )
    claim = evaluate_participation_claim(rows)
    simulator_report = build_simulator_validation_report(
        pack, calibration, results[0], claim, base.experiment.target_asset
    )
    release_report = build_release_validation_report(pack)
    failure = build_failure_surface(rows)
    orders: list[dict] = []
    trades: list[dict] = []
    snapshots: list[dict] = []
    events: list[dict] = []
    states: list[dict] = []
    regimes: list[dict] = []
    labels: list[dict] = []
    for row, spec, result in zip(rows, specs, results, strict=True):
        identity = {
            "run_id": row["run_id"],
            "seed": row["seed"],
            "participation_rate": row["participation_rate"],
            "calibration_parameter_set_id": row["calibration_parameter_set_id"],
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
        regimes.append({**identity, **spec.macro.model_dump(mode="json"), "world_type": spec.world_type})
        labels.append(
            {
                **identity,
                **spec.interventions.model_dump(mode="json"),
                "ground_truth": json.dumps(spec.ground_truth_labels, sort_keys=True),
            }
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

    representative = specs[0]
    (destination / "world_spec.yaml").write_text(representative.to_yaml())
    write_json(destination / "world_spec.json", representative.model_dump(mode="json"))
    write_json(destination / "run_summary.json", rows)
    write_json(destination / "metrics.json", {"claim_gate": claim.model_dump(mode="json"), "runs": rows})
    write_json(
        destination / "realism_report.json",
        {
            "classification": "replaced by fit-for-use validation",
            "vectors": [vector.model_dump(mode="json") for vector in simulator_report.vectors],
            "disclaimer": "No blanket realism claim is made.",
        },
    )
    write_json(destination / "failure_surface.json", failure)
    write_json(
        destination / "calibration_pack.json",
        {"pack": pack.model_dump(mode="json"), "calibration": calibration.model_dump(mode="json")},
    )
    write_json(
        destination / "intervention_results.json", {"claim_gate": claim.model_dump(mode="json"), "runs": rows}
    )
    write_json(destination / "simulator_validation_report.json", simulator_report.model_dump(mode="json"))
    write_json(
        destination / "synthetic_release_validation_report.json", release_report.model_dump(mode="json")
    )
    write_parquet(destination / "orders.parquet", orders)
    write_parquet(destination / "trades.parquet", trades)
    write_parquet(destination / "book_snapshots.parquet", snapshots)
    write_parquet(destination / "events.parquet", events)
    write_parquet(destination / "agent_states.parquet", states)
    write_parquet(destination / "latent_regimes.parquet", regimes)
    write_parquet(destination / "intervention_labels.parquet", labels)
    report_lines = [
        "# Calibrated Market Validation experiment",
        "",
        f"Calibration pack: `{pack.pack_id}`",
        f"Paired runs: {len(rows)} across {len(sets)} accepted calibration sets and {len(seeds)} common seeds.",
        "",
        "## Execution stress-testing verdict",
        "",
        f"`{simulator_report.use_case.verdict}`",
        "",
        "## Permitted claims",
        "",
        *[f"- {item}" for item in simulator_report.permitted_claims],
        "",
        "## Blocked claims",
        "",
        *[f"- {item}" for item in simulator_report.blocked_claims],
    ]
    (destination / "report.md").write_text("\n".join(report_lines) + "\n")
    package_files = [
        filename
        for filename in VALIDATION_REQUIRED_FILES
        if filename not in {"manifest.json", "synthetic_market_package_manifest.json"}
    ]
    package_manifest = {
        "package_type": "SyntheticMarketPackage",
        "package_version": "1.0",
        "content_id": hashlib.sha256(
            json.dumps(
                {
                    "world_hash": representative.specification_hash(),
                    "pack_id": pack.pack_id,
                    "parameter_sets": [item.parameter_set_id for item in sets],
                    "seeds": seeds,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "calibration_pack_id": pack.pack_id,
        "contains_source_rows": False,
        "membership_inference": "NOT_APPLICABLE",
        "files": {filename: sha256(destination / filename) for filename in package_files},
    }
    write_json(destination / "synthetic_market_package_manifest.json", package_manifest)
    manifest: dict[str, Any] = {
        "experiment_id": experiment_id,
        "created_at": datetime.now(UTC).isoformat(),
        "code_commit": _commit(),
        "world_hash": representative.specification_hash(),
        "calibration_pack_id": pack.pack_id,
        "calibration_parameter_set_ids": [item.parameter_set_id for item in sets],
        "seeds": seeds,
        "engine_backend": "internal_exact_clob",
        "order_flow_provider": "queue_reactive",
        "world_type": "emergent_calibrated",
        "python": platform.python_version(),
        "mean_runtime_ms": statistics.fmean(result.runtime_ms for result in results),
        "reproduction_command": f"smw validate-market configs/presets/fragile-small-cap.yaml --mode {mode}",
        "artifact_hashes": {},
    }
    for filename in VALIDATION_REQUIRED_FILES:
        if filename != "manifest.json":
            manifest["artifact_hashes"][filename] = sha256(destination / filename)
    write_json(destination / "manifest.json", manifest)
    calibration_payload = {
        "pack": pack.model_dump(mode="json"),
        "calibration": calibration.model_dump(mode="json"),
    }
    summary_runs = []
    for participation in participations:
        selected = [row for row in rows if row["participation_rate"] == participation]
        summary_runs.append(
            {
                "scenario": f"participation_{int(participation * 100):02d}pct",
                "participation_rate": participation,
                "description": "50% displayed-depth reduction with a 3,000-share forced seller",
                "metrics": {
                    "fill_rate": statistics.fmean(float(row["metrics"]["fill_rate"]) for row in selected),
                    "implementation_shortfall_bps": statistics.fmean(
                        float(row["implementation_shortfall_bps"]) for row in selected
                    ),
                },
            }
        )
    intervention_summary = {
        "runs": summary_runs,
        "raw_runs": rows,
        "claim_gate": claim.model_dump(mode="json"),
    }
    return ValidationCampaignResult(
        experiment_id,
        destination,
        calibration_payload,
        intervention_summary,
        simulator_report.model_dump(mode="json"),
        release_report.model_dump(mode="json"),
        manifest,
    )


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
