from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas import WorldSpec
from app.simulation import run_simulation
from app.world import build_demo_world

EXPORT_SCHEMA = "financial-world-release/v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unavailable"


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _world_version(spec: WorldSpec) -> str:
    return f"{spec.schema_version}-{spec.specification_hash()[:16]}"


def _public_entity(asset: Any, world: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": f"synthetic:company:{asset.ticker}",
        "symbol": asset.ticker,
        "display_name": asset.company_name,
        "sector": asset.sector,
        "world": world,
    }


def _financial_observations(asset: Any, world: dict[str, Any], producer: dict[str, Any]) -> list[dict[str, Any]]:
    accounting = asset.accounting
    if accounting is None:
        raise ValueError(f"asset {asset.ticker} has no accounting state")
    observed = accounting.period_end
    available = accounting.available_at
    entity_id = f"synthetic:company:{asset.ticker}"
    provenance = {
        "world": world,
        "producer": producer,
        "artifact": "public/financials.json",
        "source_record": f"accounting:{asset.ticker}:{accounting.period}",
    }
    gross_profit = accounting.revenue - accounting.cogs
    operating_income = gross_profit - accounting.operating_expenses
    values = {
        "revenue": (accounting.revenue, "USD", None),
        "cogs": (accounting.cogs, "USD", None),
        "gross_profit": (gross_profit, "USD", "revenue - cogs"),
        "operating_expenses": (accounting.operating_expenses, "USD", None),
        "operating_income": (operating_income, "USD", "gross_profit - operating_expenses"),
        "operating_margin": (operating_income / accounting.revenue, "ratio", "operating_income / revenue"),
        "net_income": (accounting.net_income, "USD", None),
        "assets": (accounting.assets, "USD", None),
        "liabilities": (accounting.liabilities, "USD", None),
        "equity": (accounting.equity, "USD", None),
        "weighted_shares": (accounting.weighted_shares, "shares", None),
    }
    rows = []
    for metric, (value, unit, calculation) in values.items():
        rows.append({
            "entity_id": entity_id,
            "metric": metric,
            "value": round(float(value), 12),
            "unit": unit,
            "period": accounting.period,
            "observation_at": observed,
            "available_at": available,
            "retrieved_at": available,
            "source": "market-fuzzer-native",
            "source_record": provenance["source_record"],
            "calculation": calculation,
            "world": world,
            "provenance": provenance,
            "quality": {"status": "calculated" if metric == "operating_margin" else "observed", "confidence": 1.0},
        })
    return rows


def _price_rows(spec: WorldSpec, result: Any, world: dict[str, Any], producer: dict[str, Any]) -> list[dict[str, Any]]:
    asset = next(asset for asset in spec.assets if asset.ticker == spec.experiment.target_asset)
    values = [int(row["asset_states"][asset.ticker]["mid_ticks"]) for row in result.timeline]
    start = spec.clock.start
    rows = []
    for index, close in enumerate(values):
        open_price = values[index - 1] if index else close
        high = max(open_price, close) + 1
        low = max(1, min(open_price, close) - 1)
        timestamp = start + timedelta(seconds=spec.clock.step_seconds * index)
        session = timestamp.date().isoformat()
        rows.append({
            "security": asset.ticker,
            "session": session,
            "observation_at": timestamp.isoformat().replace("+00:00", "Z"),
            "available_at": timestamp.isoformat().replace("+00:00", "Z"),
            "open": float(open_price) * spec.exchange.tick_size_cents / 100,
            "high": float(high) * spec.exchange.tick_size_cents / 100,
            "low": float(low) * spec.exchange.tick_size_cents / 100,
            "close": float(close) * spec.exchange.tick_size_cents / 100,
            "unit": "USD/share",
            "volume": int(result.timeline[index]["asset_states"][asset.ticker].get("volume", 0)),
            "world": world,
            "provenance": {"producer": producer, "artifact": "public/prices.json", "source_record": f"bar:{asset.ticker}:{index}"},
        })
    # The exchange is intraday; the contract requires one canonical bar per
    # security/session. Aggregate the deterministic session into one OHLCV row.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["session"], []).append(row)
    aggregated = []
    for session, session_rows in sorted(grouped.items()):
        aggregated.append({
            **session_rows[-1],
            "observation_at": session_rows[0]["observation_at"],
            "available_at": session_rows[-1]["available_at"],
            "open": session_rows[0]["open"],
            "high": max(row["high"] for row in session_rows),
            "low": min(row["low"] for row in session_rows),
            "close": session_rows[-1]["close"],
            "volume": sum(row["volume"] for row in session_rows),
            "provenance": {"producer": producer, "artifact": "public/prices.json", "source_record": f"session:{asset.ticker}:{session}"},
        })
    return aggregated


def export_world(output: Path, world_id: str = "test-world-001", seed: int = 42) -> Path:
    spec = build_demo_world(seed=seed)
    data = spec.model_dump(mode="python")
    data["world_id"] = world_id
    # One short deterministic session keeps the native smoke cheap while still
    # exercising the actual exchange and simulation state.
    data["clock"]["end"] = data["clock"]["start"] + timedelta(seconds=30 * 60)
    data["experiment"]["target_asset"] = "NOVA"
    data["experiment"]["parent_order"]["quantity"] = 600
    spec = WorldSpec.model_validate(data)
    result = run_simulation(spec, collect_agent_states=True, collect_strategy_steps=False)
    producer = {"name": "market-fuzzer", "schema": EXPORT_SCHEMA, "git_sha": _git_sha(), "seed": seed, "config_hash": spec.specification_hash()}
    world = {"world_type": "synthetic", "world_id": world_id, "version": _world_version(spec)}
    output.mkdir(parents=True, exist_ok=True)
    (output / "public").mkdir(exist_ok=True)
    (output / "hidden").mkdir(exist_ok=True)
    entities = [_public_entity(asset, world) for asset in spec.assets]
    financials = [row for asset in spec.assets for row in _financial_observations(asset, world, producer)]
    prices = _price_rows(spec, result, world, producer)
    public_events = [event for event in result.events if event.get("public_or_private", "public") == "public"]
    hidden = {
        "world": world,
        "producer": producer,
        "world_spec": spec.model_dump(mode="json"),
        "timeline": result.timeline,
        "events": result.events,
        "agent_states": result.agent_states,
        "hidden_truth": {"fundamentals": {asset.ticker: asset.initial_fundamental_value_ticks for asset in spec.assets}, "seed": seed},
    }
    _json(output / "public" / "entities.json", {"world": world, "entities": entities})
    _json(output / "public" / "financials.json", {"world": world, "observations": financials})
    _json(output / "public" / "prices.json", {"world": world, "prices": prices})
    _json(output / "public" / "events.json", {"world": world, "events": public_events})
    _json(output / "hidden" / "world_state.json", hidden)
    artifacts = {}
    for relative in ("public/entities.json", "public/financials.json", "public/prices.json", "public/events.json", "hidden/world_state.json"):
        artifacts[relative] = {"path": relative, "sha256": _sha256(output / relative), "bytes": (output / relative).stat().st_size, "visibility": "hidden" if relative.startswith("hidden/") else "public"}
    manifest = {
        "schema_version": "1",
        "world": world,
        "world_id": world_id,
        "world_version": world["version"],
        "seed": seed,
        "generated_at": "deterministic-test-export",
        "producer": producer,
        "generator": producer,
        "assets": entities,
        "artifacts": {"public": [key for key in artifacts if key.startswith("public/")], "hidden": [key for key in artifacts if key.startswith("hidden/")]},
        "public_artifacts": [key for key in artifacts if key.startswith("public/")],
        "hidden_artifacts": [key for key in artifacts if key.startswith("hidden/")],
        "artifact_hashes": artifacts,
        "release_status": "native-exported",
    }
    _json(output / "manifest.json", manifest)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a deterministic native Market Fuzzer world")
    parser.add_argument("--world-id", default="test-world-001")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(export_world(args.output, args.world_id, args.seed))


if __name__ == "__main__":
    main()
