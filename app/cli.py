from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
import yaml

from app.compiler import compile_world
from app.experiments import run_batch, run_single
from app.schemas import WorldSpec
from app.world import build_demo_world

cli = typer.Typer(help="Synthetic Market World Engine")


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


@cli.command()
def report(experiment_id: str) -> None:
    path = Path("artifacts") / experiment_id / "report.md"
    if not path.exists():
        raise typer.BadParameter("experiment report not found")
    typer.echo(path.read_text())


@cli.command()
def demo(serve: bool = typer.Option(False, help="Start the browser app after generating artifacts")) -> None:
    result = run_batch(build_demo_world(), quick=True)
    typer.echo(f"No-key demo complete: {result.artifact_dir}")
    if serve:
        typer.echo("Opening app at http://127.0.0.1:8000")
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000)


def entrypoint() -> None:
    cli()


if __name__ == "__main__":
    entrypoint()
