"""Export MARKET_FUZZER_WORLD_V2 worlds as financial-world-release/v2 artifacts.

Public artifact  : what Zion would ingest (entities, financials, prices,
                   events, filings, estimates) — every row PIT-stamped and
                   carrying the synthetic world selector.
Hidden artifact  : latent truth + causal ground truth (interventions,
                   counterfactual-ready state), sealed from the public side.
Manifest         : producer metadata + sha256 of every artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from app.economy.v2 import EconomyParamsV2, InterventionV2, WorldOutcomeV2, run_economy

EXPORT_SCHEMA = "financial-world-release/v2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unavailable"


def _iso(d: date) -> str:
    return d.isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _world_block(outcome: WorldOutcomeV2) -> dict[str, Any]:
    return {
        "world_type": "synthetic",
        "world_id": outcome.world_id,
        "version": f"world-v2-{outcome.params.seed}-{outcome.params.years}y",
        "generator": "market-fuzzer-world-v2",
        "seed": outcome.seed,
    }


def _provenance(world: dict[str, Any], artifact: str) -> dict[str, Any]:
    return {
        "world": world,
        "producer": {
            "name": "market-fuzzer-world-v2",
            "schema": EXPORT_SCHEMA,
            "git_sha": _git_sha(),
        },
        "artifact": artifact,
    }


def _provenance_with_seed(world: dict[str, Any], artifact: str, source_record: str) -> dict[str, Any]:
    prov = _provenance(world, artifact)
    prov["source_record"] = source_record
    return prov


def export_economy_v2(
    outcome: WorldOutcomeV2,
    output: Path,
    interventions_text: tuple[str, ...] = (),
    generated_at: str = "deterministic",
) -> Path:
    """Write public/, hidden/, manifest.json for a completed V2 world."""
    world = _world_block(outcome)
    output.mkdir(parents=True, exist_ok=True)
    (output / "public").mkdir(exist_ok=True)
    (output / "hidden").mkdir(exist_ok=True)

    # ---------------- public artifacts ---------------- #
    entities = [
        {
            "entity_id": f"synthetic:company:{c['ticker']}",
            "symbol": c["ticker"],
            "display_name": c["name"],
            "sector": c["sector"],
            "world": world,
        }
        for c in outcome.companies
    ]

    financials: list[dict[str, Any]] = []
    for q in outcome.quarters:
        prov = _provenance_with_seed(
            world, "public/financials.json", f"quarter:{q.company}:{q.fiscal_year}Q{q.fiscal_quarter}"
        )
        period = f"{q.fiscal_year}Q{q.fiscal_quarter}"
        rows = {
            "revenue": (q.revenue, "USD", None),
            "cogs": (q.cogs, "USD", None),
            "gross_profit": (q.gross_profit, "USD", "revenue - cogs"),
            "operating_expenses": (q.operating_expenses, "USD", None),
            "operating_income": (q.ebit, "USD", "gross_profit - operating_expenses"),
            "interest_expense": (q.interest_expense, "USD", None),
            "pretax_income": (q.pretax_income, "USD", None),
            "tax_expense": (q.tax_expense, "USD", None),
            "net_income": (q.net_income, "USD", None),
            "eps": (q.eps, "USD/share", "net_income / weighted_shares"),
            "gross_margin": (q.gross_margin, "ratio", "gross_profit / revenue"),
            "operating_margin": (q.operating_margin, "ratio", "operating_income / revenue"),
        }
        for metric, (value, unit, calc) in rows.items():
            financials.append(
                {
                    "entity_id": f"synthetic:company:{q.company}",
                    "metric": metric,
                    "value": round(float(value), 12),
                    "unit": unit,
                    "period": period,
                    "period_end": _iso(q.period_end),
                    "observation_at": _iso(q.period_end),
                    "available_at": _iso(q.available_at),
                    "retrieved_at": _iso(q.available_at),
                    "calculation": calc,
                    "world": world,
                    "provenance": prov,
                    "quality": {"status": "observed", "confidence": 1.0},
                }
            )

    # balance-sheet metric rows so downstream QC can verify the identity
    period_by_key = {
        (q.company, q.period_end): f"{q.fiscal_year}Q{q.fiscal_quarter}" for q in outcome.quarters
    }
    for b in outcome.balance_sheets:
        period = period_by_key.get((b.company, b.period_end))
        if period is None:
            continue
        prov = _provenance_with_seed(world, "public/financials.json", f"balance:{b.company}:{period}")
        rows = {
            "assets": (b.assets, "USD", None),
            "liabilities": (b.liabilities, "USD", None),
            "equity": (b.equity, "USD", "assets - liabilities"),
            "cash": (b.cash, "USD", None),
            "debt": (b.debt, "USD", None),
        }
        for metric, (value, unit, calc) in rows.items():
            financials.append(
                {
                    "entity_id": f"synthetic:company:{b.company}",
                    "metric": metric,
                    "value": round(float(value), 12),
                    "unit": unit,
                    "period": period,
                    "period_end": _iso(b.period_end),
                    "observation_at": _iso(b.period_end),
                    "available_at": _iso(b.available_at),
                    "retrieved_at": _iso(b.available_at),
                    "calculation": calc,
                    "world": world,
                    "provenance": prov,
                    "quality": {"status": "observed", "confidence": 1.0},
                }
            )

    prices = [
        {
            "security": p.company,
            "entity_id": f"synthetic:company:{p.company}",
            "session": _iso(p.session),
            "observation_at": _iso(p.session),
            "available_at": _iso(p.session),
            "open": p.open,
            "high": p.high,
            "low": p.low,
            "close": p.close,
            "unit": "USD/share",
            "volume": round(p.volume, 0),
            "world": world,
            "provenance": _provenance_with_seed(
                world, "public/prices.json", f"bar:{p.company}:{_iso(p.session)}"
            ),
        }
        for p in outcome.prices
    ]

    filings = [
        {
            "entity_id": f"synthetic:company:{f.company}",
            "form": f.kind,
            "filed_at": _iso(f.filed_at),
            "available_at": _iso(f.available_at),
            "period_end": _iso(f.period_end),
            "world": world,
            "provenance": _provenance_with_seed(
                world, "public/filings.json", f"filing:{f.company}:{_iso(f.filed_at)}"
            ),
        }
        for f in outcome.filings
    ]

    estimates = [
        {
            "entity_id": f"synthetic:company:{e.company}",
            "metric": e.metric,
            "period": e.period,
            "source": e.source,
            "estimate": round(e.estimate, 6),
            "issued_at": _iso(e.issued_at),
            "revised_at": _iso(e.revised_at),
            "world": world,
            "provenance": _provenance_with_seed(
                world, "public/estimates.json", f"estimate:{e.company}:{e.period}"
            ),
        }
        for e in outcome.estimates
    ]

    events = [
        {
            "at": _iso(ev.at),
            "entity_id": f"synthetic:company:{ev.company}" if ev.company else None,
            "kind": ev.kind,
            "payload": ev.payload,
            "world": world,
            "provenance": _provenance_with_seed(
                world, "public/events.json", f"event:{ev.kind}:{ev.company}:{_iso(ev.at)}"
            ),
        }
        for ev in outcome.events
    ]

    _write_json(output / "public" / "entities.json", {"world": world, "entities": entities})
    _write_json(output / "public" / "financials.json", {"world": world, "observations": financials})
    _write_json(output / "public" / "prices.json", {"world": world, "prices": prices})
    _write_json(output / "public" / "filings.json", {"world": world, "filings": filings})
    _write_json(output / "public" / "estimates.json", {"world": world, "estimates": estimates})
    _write_json(output / "public" / "events.json", {"world": world, "events": events})

    # ---------------- hidden artifact ---------------- #
    hidden = {
        "world": world,
        "params": {
            "years": outcome.params.years,
            "seed": outcome.params.seed,
            "start_year": outcome.params.start_year,
            "tax_rate": outcome.params.tax_rate,
            "payout_ratio": outcome.params.payout_ratio,
            "capex_rate": outcome.params.capex_rate,
        },
        "macro": [
            {
                "date": _iso(m.date),
                "gdp_growth": m.gdp_growth,
                "inflation": m.inflation,
                "policy_rate": m.policy_rate,
                "regime": m.regime,
                "credit_index": m.credit_index,
            }
            for m in outcome.macro
        ],
        "latents": {
            ticker: [{"date": _iso(snap.date), "values": snap.values} for snap in snaps]
            for ticker, snaps in outcome.latents.items()
        },
        "defaults": outcome.defaults,
        "fraud_windows": outcome.fraud_windows,
        "interventions": [
            {
                "company": iv.company,
                "variable": iv.variable,
                "value": iv.value,
                "start": _iso(iv.start),
                "end": _iso(iv.end) if iv.end else None,
            }
            for iv in outcome.interventions
        ],
        "causal_notes": list(interventions_text),
        "balance_identity": "assets = liabilities + equity (exact; emitted residual ~ 0)",
        "plug_definition": (
            "financing gap absorbed when cash would go negative; balance identity "
            "still exact because equity absorbs the plug"
        ),
    }
    _write_json(output / "hidden" / "world_state.json", hidden)

    # ---------------- manifest ---------------- #
    artifact_files = [
        "public/entities.json",
        "public/financials.json",
        "public/prices.json",
        "public/filings.json",
        "public/estimates.json",
        "public/events.json",
        "hidden/world_state.json",
    ]
    artifacts: dict[str, Any] = {}
    for rel in artifact_files:
        artifacts[rel] = {
            "path": rel,
            "sha256": _sha256(output / rel),
            "bytes": (output / rel).stat().st_size,
            "visibility": "hidden" if rel.startswith("hidden/") else "public",
        }
    manifest = {
        "schema": EXPORT_SCHEMA,
        "schema_version": "2",
        "world": world,
        "world_id": outcome.world_id,
        "world_version": world["version"],
        "seed": outcome.seed,
        "generated_at": generated_at,
        "producer": {
            "name": "market-fuzzer-world-v2",
            "schema": EXPORT_SCHEMA,
            "git_sha": _git_sha(),
        },
        "generator": {
            "name": "market-fuzzer-world-v2",
            "schema": EXPORT_SCHEMA,
            "git_sha": _git_sha(),
        },
        "assets": entities,
        "artifacts": {
            "public": [
                "public/entities.json",
                "public/financials.json",
                "public/prices.json",
                "public/filings.json",
                "public/estimates.json",
                "public/events.json",
            ],
            "hidden": ["hidden/world_state.json"],
        },
        "public_artifacts": [
            "public/entities.json",
            "public/financials.json",
            "public/prices.json",
            "public/filings.json",
            "public/estimates.json",
            "public/events.json",
        ],
        "hidden_artifacts": ["hidden/world_state.json"],
        "company_count": len(outcome.companies),
        "quarter_count": len(outcome.quarters),
        "artifact_hashes": artifacts,
        "release_status": "world-v2-exported",
    }
    _write_json(output / "manifest.json", manifest)
    return output


def export_world_v2(
    output: Path,
    world_id: str = "fuzzer-000000",
    seed: int = 20260921,
    years: int = 8,
    interventions: tuple[InterventionV2, ...] = (),
    causal_notes: tuple[str, ...] = (),
    generated_at: str = "deterministic",
) -> Path:
    """Run the V2 economy and export a complete release in one call."""
    params = EconomyParamsV2(years=years, seed=seed)
    outcome = run_economy(params, interventions, world_id)
    return export_economy_v2(outcome, output, causal_notes, generated_at=generated_at)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a MARKET_FUZZER_WORLD_V2 release")
    parser.add_argument("--world-id", default="fuzzer-000000")
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument("--years", type=int, default=8)
    parser.add_argument("--generated-at", default="deterministic")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--intervene",
        action="append",
        default=[],
        help="company:variable:value:YYYY-MM-DD (repeatable)",
    )
    args = parser.parse_args()

    ivs: list[InterventionV2] = []
    for raw in args.intervene:
        company, variable, value, start = raw.split(":")
        ivs.append(
            InterventionV2(
                company=company,
                variable=variable,
                value=float(value),
                start=date.fromisoformat(start),
            )
        )
    path = export_world_v2(
        args.output,
        world_id=args.world_id,
        seed=args.seed,
        years=args.years,
        interventions=tuple(ivs),
        generated_at=args.generated_at,
    )
    print(path)


if __name__ == "__main__":
    main()
