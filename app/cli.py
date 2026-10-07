from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
import yaml

from app.calibration import build_demo_calibration_pack, calibrate_bootstrap, compile_canonical_csv
from app.compiler import compile_world
from app.experiments import run_batch, run_single, run_validation_campaign
from app.product import DEFAULT_PROPERTIES, STRATEGIES, evaluate, export_fixture, run_search, scenario_hash
from app.schemas import WorldSpec
from app.world import build_demo_world

cli = typer.Typer(help="Market Fuzzer deterministic strategy regression harness")


def load_spec(path: Path) -> WorldSpec:
    if path.stat().st_size > 1_000_000:
        raise typer.BadParameter("specification exceeds 1 MB")
    raw = (
        yaml.safe_load(path.read_text()) if path.suffix in {".yaml", ".yml"} else json.loads(path.read_text())
    )
    return WorldSpec.model_validate(raw)


@cli.command()
def validate(path: Path) -> None:
    spec = load_spec(path)
    typer.echo(f"valid {spec.specification_hash()}")


@cli.command("compile")
def compile_command(prompt: str = typer.Option(...), offline: bool = True, seed: int = 42) -> None:
    result = compile_world(prompt, seed, "offline" if offline else "gpt")
    typer.echo(result.spec.to_yaml())
    typer.echo(f"# spec_hash: {result.spec_hash}")


@cli.command()
def run(path: Path) -> None:
    result = run_single(load_spec(path))
    typer.echo(json.dumps({"result_hash": result.result_hash, "summary": result.summary}, indent=2))


@cli.command()
def batch(path: Path, seed: int | None = None) -> None:
    spec = load_spec(path)
    if seed is not None:
        data = spec.model_dump()
        data["seed"] = seed
        spec = WorldSpec.model_validate(data)
    result = run_batch(spec)
    typer.echo(
        json.dumps(
            {
                "experiment_id": result.experiment_id,
                "artifact_dir": str(result.artifact_dir),
                "worst": result.failure_surface["worst"],
            },
            indent=2,
        )
    )


@cli.command("calibrate")
def calibrate_command(source: Path | None = None, mode: str = "quick") -> None:
    if mode not in {"quick", "audit"}:
        raise typer.BadParameter("mode must be quick or audit")
    pack = compile_canonical_csv(source) if source else build_demo_calibration_pack()
    calibration = calibrate_bootstrap(pack, mode=mode)  # type: ignore[arg-type]
    typer.echo(
        json.dumps(
            {"pack": pack.model_dump(mode="json"), "calibration": calibration.model_dump(mode="json")},
            indent=2,
        )
    )


@cli.command("validate-market")
def validate_market(path: Path, mode: str = "quick") -> None:
    if mode not in {"quick", "audit"}:
        raise typer.BadParameter("mode must be quick or audit")
    result = run_validation_campaign(load_spec(path), mode=mode)
    typer.echo(
        json.dumps(
            {
                "experiment_id": result.experiment_id,
                "artifact_dir": str(result.artifact_dir),
                "simulator_verdict": result.simulator_validation_report["overall_verdict"],
                "execution_stress_testing": result.simulator_validation_report["use_case"],
                "release_permitted": result.synthetic_release_validation_report["release_permitted"],
            },
            indent=2,
        )
    )


@cli.command()
def report(experiment_id: str) -> None:
    path = Path("artifacts") / experiment_id / "report.md"
    if not path.exists():
        raise typer.BadParameter("experiment report not found")
    typer.echo(path.read_text())


@cli.command("test")
def test_fixture(path: Path) -> None:
    """Run a Market Fuzzer YAML/JSON regression fixture."""
    if path.is_dir():
        candidates = sorted([*path.glob("*.yaml"), *path.glob("*.yml"), *path.glob("*.json")])
        results = []
        for item in candidates:
            try:
                results.append(_run_fixture_data(item))
            except (KeyError, TypeError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
                results.append({"path": str(item), "result": "invalid", "error": str(exc)})
        typer.echo(json.dumps({"total": len(results), "results": results}, indent=2))
        if any(
            item.get("result") == "invalid" or not item.get("matches_expected_outcome") for item in results
        ):
            raise typer.Exit(1)
        return
    try:
        result = _run_fixture_data(path)
    except (KeyError, TypeError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
        typer.echo(json.dumps({"path": str(path), "result": "invalid", "error": str(exc)}, indent=2))
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))
    if not result["matches_expected_outcome"]:
        raise typer.Exit(1)


def _run_fixture_data(path: Path) -> dict:
    if not path.exists() or not path.is_file():
        raise ValueError("fixture path does not exist or is not a file")
    if path.stat().st_size > 1_000_000:
        raise ValueError("fixture exceeds 1 MB")
    data = (
        yaml.safe_load(path.read_text()) if path.suffix in {".yaml", ".yml"} else json.loads(path.read_text())
    )
    if not isinstance(data, dict):
        raise ValueError("fixture root must be an object")
    if data.get("schema_version") != "1.1":
        raise ValueError("unsupported fixture schema version; expected 1.1")
    for field in ("case", "scenario_hash", "strategy", "market", "seeds", "safety_properties", "expected"):
        if field not in data:
            raise ValueError(f"fixture missing required field: {field}")
    if not isinstance(data["case"], dict) or not data["case"].get("id"):
        raise ValueError("fixture case.id is required")
    if data["scenario_hash"] != scenario_hash(data["market"]):
        raise ValueError("fixture scenario_hash does not match market parameters")
    if not isinstance(data["strategy"], dict):
        raise ValueError("fixture strategy must be an object")
    strategy_id = data["strategy"].get("id")
    if strategy_id not in STRATEGIES:
        raise ValueError("fixture has an unknown strategy id")
    expected_identity = STRATEGIES[strategy_id]
    if data["strategy"].get("type") != expected_identity["type"]:
        raise ValueError("fixture strategy type does not match the stored strategy id")
    if data["strategy"].get("version") != expected_identity["version"]:
        raise ValueError("fixture strategy version does not match the stored strategy id")
    if not isinstance(data["strategy"].get("parameters"), dict):
        raise ValueError("fixture strategy.parameters must be an object")
    if not isinstance(data["seeds"], list) or not data["seeds"]:
        raise ValueError("fixture seeds must be a non-empty list")
    if any(not isinstance(seed, int) for seed in data["seeds"]):
        raise ValueError("fixture seeds must contain integers")
    if not isinstance(data["safety_properties"], list) or not data["safety_properties"]:
        raise ValueError("fixture safety_properties must be a non-empty list")
    expected = data["expected"]
    if not isinstance(expected, dict) or expected.get("result") not in {"pass", "fail"}:
        raise ValueError("fixture expected.result must be pass or fail")
    targeted_property = expected.get("targeted_property")
    targeted_result = expected.get("targeted_result")
    if targeted_property is not None and targeted_result not in {"pass", "fail"}:
        raise ValueError("fixture expected.targeted_result must be pass or fail")
    strategy = {"id": strategy_id, **expected_identity, "parameters": data["strategy"]["parameters"]}
    props = data.get("safety_properties", DEFAULT_PROPERTIES)
    seeds = data.get("seeds", [42])
    results = [evaluate(strategy, data["market"], props, int(seed)) for seed in seeds]
    actual = "pass" if all(result["passed"] for result in results) else "fail"
    targeted_actual = None
    if targeted_property:
        target_rows = [
            next((row for row in result["properties"] if row["id"] == targeted_property), None)
            for result in results
        ]
        if any(row is None for row in target_rows):
            raise ValueError(f"fixture targeted property is not enabled: {targeted_property}")
        target_passed = [bool(row["passed"]) for row in target_rows if row is not None]
        targeted_actual = "pass" if all(target_passed) else "fail" if not any(target_passed) else "mixed"
    matches = actual == expected["result"] and (targeted_result is None or targeted_actual == targeted_result)
    return {
        "path": str(path),
        "result": actual,
        "expected": expected["result"],
        "strategy_id": strategy_id,
        "targeted_property": targeted_property,
        "targeted_result": targeted_actual,
        "seeds": seeds,
        "matches_expected_outcome": matches,
    }


@cli.command()
def demo(serve: bool = typer.Option(False, help="Start the browser app after generating artifacts")) -> None:
    result = run_batch(build_demo_world(), quick=True)
    typer.echo(f"No-key demo complete: {result.artifact_dir}")
    if serve:
        typer.echo("Opening app at http://127.0.0.1:8000")
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000)


@cli.command("run-example")
def run_example() -> None:
    """Run the complete no-key Market Fuzzer POV workflow."""
    fragile = {
        "id": "pov_fragile",
        **STRATEGIES["pov_fragile"],
        "parameters": STRATEGIES["pov_fragile"]["defaults"],
    }
    corrected = {"id": "pov", **STRATEGIES["pov"], "parameters": STRATEGIES["pov"]["defaults"]}
    baseline = evaluate(
        fragile,
        {"liquidity": 1, "volatility": 1, "latency_ms": 10, "forced_seller": 0, "spread": 1},
        DEFAULT_PROPERTIES,
        42,
    )
    failure = run_search(fragile, DEFAULT_PROPERTIES, mode="quick")
    if not baseline["passed"] or not failure.get("found"):
        raise typer.Exit(1)
    seeds = failure["reproduction"]["seeds_tested"]
    corrected_runs = [evaluate(corrected, failure["minimized"], DEFAULT_PROPERTIES, seed) for seed in seeds]
    if not all(result["passed"] for result in corrected_runs):
        raise typer.Exit(1)
    exported = export_fixture(failure, fragile, DEFAULT_PROPERTIES)
    typer.echo(
        json.dumps(
            {
                "baseline": "PASS",
                "failure": "FOUND",
                "targeted_property": failure["violated_property"]["id"],
                "minimized": failure["minimized"],
                "minimized_severity": failure["severity"],
                "passing_neighbor": failure["passing_neighbor"],
                "reproduction": failure["reproduction"],
                "corrected": "PASS",
                "fixture_yaml": exported["yaml"],
                "fixture_json": exported["json"],
                "regression_command": f"smw test {exported['yaml']}",
            },
            indent=2,
        )
    )


benchmark_app = typer.Typer(help="Synthetic exchange benchmark (M10.6 process-family holdout)")


@benchmark_app.command("run")
def benchmark_run(
    task: Annotated[str, typer.Option(help="execution | market_making | portfolio")] = "execution",
    agent: Annotated[str | None, typer.Option(help="external agent HTTP endpoint")] = None,
    worlds: Annotated[int, typer.Option(min=1, help="number of sealed evaluation worlds")] = 32,
    securities: Annotated[int, typer.Option(min=1)] = 8,
    days: Annotated[int, typer.Option(min=1)] = 5,
    steps_per_day: Annotated[int, typer.Option(min=1)] = 30,
    seed: Annotated[int, typer.Option()] = 20_261_006,
    policy: Annotated[str, typer.Option(help="builtin policy when --agent is absent")] = "twap",
    output: Annotated[Path | None, typer.Option(help="write the replay package JSON here")] = None,
    allow_invalid: Annotated[
        bool,
        typer.Option(help="exit 0 even when the agent failed; the run stays labelled non-scoreable"),
    ] = False,
) -> None:
    """Run a synthetic exchange benchmark against a sealed set of worlds.

    Exit codes: 0 = valid benchmark completed, 1 = internal benchmark failure,
    2 = external agent unavailable or protocol-invalid.
    """

    from app.benchmark import TaskKind, run_benchmark
    from app.benchmark.port import accumulate_port, passive_maker_port, twap_port

    try:
        kind = TaskKind(task)
    except ValueError as exc:
        raise typer.BadParameter(f"unknown task {task!r}") from exc
    if agent is not None:
        from app.benchmark.port import HttpJsonPort

        def port_factory(_index: int) -> object:
            return HttpJsonPort(agent)

    else:
        if policy not in {"twap", "maker", "accumulate"}:
            raise typer.BadParameter(f"unknown policy {policy!r}")

        def port_factory(_index: int) -> object:
            if policy == "maker":
                return passive_maker_port(spread_ticks=3, quantity=200)
            if policy == "accumulate":
                return accumulate_port(slice_quantity=1_000, max_shares_per_instrument=20_000)
            return twap_port(slice_quantity=2_500)

    try:
        report = run_benchmark(
            kind=kind,
            port_factory=port_factory,  # type: ignore[arg-type]
            worlds=worlds,
            security_count=securities,
            days=days,
            steps_per_day=steps_per_day,
            base_seed=seed,
        )
    except Exception as exc:  # any internal failure must map to the documented exit code 1
        typer.echo(f"benchmark failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(report.render())
    if output is not None:
        output.write_text(json.dumps(report.replay_package, indent=2, sort_keys=True))
        typer.echo(f"\nReplay package written to {output}")
    if report.scoreable or allow_invalid:
        return
    if report.run_status.startswith("INVALID_AGENT"):
        raise typer.Exit(code=2)
    raise typer.Exit(code=1)


cli.add_typer(benchmark_app, name="benchmark")


def entrypoint() -> None:
    cli()


if __name__ == "__main__":
    entrypoint()
