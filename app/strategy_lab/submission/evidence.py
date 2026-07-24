"""Evidence package generator for the submission run.

Writes artifacts/submission/<git-sha>/ with REAL data from the run (no fabricated
fallback rows). Also emits deck_data.json for the pitch deck, a machine-readable
CLAIMS_MANIFEST.json (honesty guardrail 5.1), and a submission_manifest.json
that binds the strategy hash, data manifest hash, claims-manifest digest, backtest
id, campaign id, and replay id.

Per spec 5.2: the claims manifest digest is bound into the SUBMISSION manifest,
NOT into the canonical strategy hash (which describes strategy semantics only).
Per spec 5.4: a per-tier watermark is derived from evidence metadata and exposed
to the deck (it is never hard-coded slide copy).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.strategy_lab.submission.orchestrator import SubmissionRun


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path.cwd())
        return out.stdout.strip()[:16]
    except Exception:
        return "unknown"


# 5.1 Machine-readable claims manifest (structured, all protected values False).
CLAIMS_MANIFEST: dict[str, Any] = {
    "live_profitability": False,
    "production_execution_fidelity": False,
    "universal_realism": False,
    "exhaustive_failure_discovery": False,
    "validated_cost_calibration": False,
    "arbitrary_strategy_support": False,
    "institutional_data_rights": False,
}


def _tier_watermark(data_mode: str, tier: int) -> str:
    """5.4 Per-tier watermark derived from evidence metadata (not slide copy)."""
    if data_mode == "fenrix" or tier == 1:
        return "TIER 1 — FENRIX ANONYMIZED / RELATIVE DATES / NON-PIT"
    if data_mode == "yfinance" or tier == 2:
        return "TIER 2 — YFINANCE RESEARCH DATA / SURVIVORSHIP-RISK / NON-EXECUTABLE"
    return "TIER 3 — SYNTHETIC FIXTURE / NOT HISTORICAL"


def _whash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def build_evidence_package(run: SubmissionRun, save_dir: str | None = None) -> dict[str, Any]:
    sha = _git_sha()
    base = Path(save_dir or f"artifacts/submission/{sha}")
    for d in ("data", "strategy", "historical", "synthetic", "replay", "demo", "pitch"):
        (base / d).mkdir(parents=True, exist_ok=True)

    bt = run.backtest
    st = run.stress

    # ---- strategy ----
    (base / "strategy" / "original_description.txt").write_text(
        "Fenrix Flagship Long/Short Momentum-Volatility strategy."
    )
    clause_ledger = {"strategy_id": run.strategy_hash, "clauses": bt.get("assets")}
    (base / "strategy" / "clause_ledger.json").write_text(json.dumps(clause_ledger, indent=2))
    (base / "strategy" / "approved_strategy.json").write_text(
        json.dumps({"strategy_id": run.strategy_hash, "approval": run.approval}, indent=2)
    )
    (base / "strategy" / "strategy_hash.txt").write_text(run.strategy_hash)

    # ---- historical (REAL data) ----
    (base / "historical" / "metrics.json").write_text(json.dumps(bt["metrics"], indent=2))
    equity_csv = "step,equity\n" + "\n".join(f"{i},{v}" for i, v in enumerate(bt["equity_curve"]))
    (base / "historical" / "equity_curve.csv").write_text(equity_csv)
    import numpy as np

    bench = bt["metrics"].get("benchmark_cagr") or 0.0
    benchmark_csv = "step,value\n" + "\n".join(f"{i},{bench}" for i in range(len(bt["equity_curve"])))
    (base / "historical" / "benchmark_curve.csv").write_text(benchmark_csv)
    np.save(base / "historical" / "weights.npy", np.asarray(bt["target_weights"]))
    trades_csv = "date,asset,side,quantity,price,commission,slippage,borrow\n" + "\n".join(
        f"{t['date']},{t['asset']},{t['side']},{t['quantity']},{t['price']},{t['commission']},{t['slippage']},{t['borrow']}"
        for t in bt["trades"]
    )
    (base / "historical" / "trades.csv").write_text(trades_csv)
    exposures_csv = "step,gross,net\n" + "\n".join(
        f"{i},{g},{n}" for i, (g, n) in enumerate(zip(bt["gross_exposure"], bt["net_exposure"], strict=False))
    )
    (base / "historical" / "exposures.csv").write_text(exposures_csv)
    costs = {
        "commission": bt["cost_summary"]["commission"],
        "slippage": bt["cost_summary"]["slippage"],
        "spread": bt["cost_summary"].get("spread", 0.0),
        "borrow": bt["cost_summary"]["borrow"],
        "locate": bt["cost_summary"].get("locate", 0.0),
        "total": bt["cost_summary"]["total"],
    }
    (base / "historical" / "costs.json").write_text(json.dumps(costs, indent=2))
    source_manifest = {
        "data_mode": bt["data_mode"],
        "tier": bt["tier"],
        "universe": bt["assets"],
        "dates": bt["dates"][:1] + bt["dates"][-1:],
        "provenance": bt["provenance"],
    }
    (base / "data" / "source_manifest.json").write_text(json.dumps(source_manifest, indent=2, default=str))
    quality = {
        "status": "ok",
        "source": bt["data_mode"],
        "tier": bt["tier"],
        "survivorship_warning": (
            "Universe fixed at selection date; delisted names absent -> survivorship bias for pre-2026 history."
            if bt["data_mode"] == "yfinance"
            else "Synthetic generated data — not historical market data."
        ),
    }
    (base / "data" / "quality_report.json").write_text(json.dumps(quality, indent=2))

    # ---- synthetic (stress) ----
    (base / "synthetic" / "campaign_public.json").write_text(
        json.dumps(
            {
                "strategy_hash": st["strategy_hash"],
                "evaluated": st["evaluated"],
                "confirmed_failure_count": st["failure_count"],
                "candidate_failure_count": st["candidate_count"],
                "predicates": st["predicates"],
            },
            indent=2,
        )
    )
    regime_rows = ["mechanism,seed,intensity,sharpe,max_drawdown,cost_pct,violated"]
    for r in st["regime_matrix"]:
        if "engine_error" in r:
            regime_rows.append(f"{r['mechanism']},{r['seed']},,,{','},{r['engine_error']}")
            continue
        viol = ";".join(r.get("violated_predicates", []))
        regime_rows.append(
            f"{r['mechanism']},{r['seed']},{r.get('intensity')},{r.get('sharpe')},{r.get('max_drawdown')},{r.get('cost_pct')},{viol}"
        )
    (base / "synthetic" / "regime_matrix.csv").write_text("\n".join(regime_rows) + "\n")
    failures_doc = {"count": st["failure_count"], "items": st["confirmed_failures"]}
    (base / "synthetic" / "failures.json").write_text(json.dumps(failures_doc, indent=2, default=str))

    # ---- replay ----
    minimized = run.minimized or {}
    adjacent = run.adjacent_pass or {}
    (base / "replay" / "minimized_failure.json").write_text(json.dumps(minimized, indent=2, default=str))
    (base / "replay" / "adjacent_pass.json").write_text(json.dumps(adjacent, indent=2, default=str))
    event_lines = [json.dumps({"type": "fill", **t}, default=str) for t in bt["trades"][:200]]
    (base / "replay" / "event_trace.jsonl").write_text("\n".join(event_lines))

    # ---- 5.1 claims manifest (structured, hashed) ----
    claims = dict(CLAIMS_MANIFEST)
    claims["cost_model_type"] = "heuristic_flat_bps"
    claims["cost_model_calibrated"] = False
    (base / "pitch" / "CLAIMS_MANIFEST.json").write_text(json.dumps(claims, indent=2))
    claims_digest = _whash(claims)

    # ---- deck data (every number the deck shows comes from here) ----
    watermark = _tier_watermark(bt["data_mode"], bt["tier"])
    deck_data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": sha,
        "strategy_hash": run.strategy_hash,
        "data_mode": run.data_mode,
        "tier": bt["tier"],
        "tier_watermark": watermark,
        "universe_size": len(bt["assets"]),
        "historical": {
            "cumulative_return": bt["metrics"]["cumulative_return"],
            "sharpe": bt["metrics"]["sharpe"],
            "cagr": bt["metrics"]["cagr"],
            "max_drawdown": bt["metrics"]["max_drawdown"],
            "volatility": bt["metrics"]["volatility"],
            "sortino": bt["metrics"]["sortino"],
            "calmar": bt["metrics"]["calmar"],
            "benchmark_cagr": bt["metrics"]["benchmark_cagr"],
            "information_ratio": bt["metrics"]["information_ratio"],
            "turnover_annualized": bt["metrics"]["turnover_annualized_avg"],
            "gross_exposure_avg": bt["metrics"]["gross_exposure_avg"],
            "net_exposure_avg": bt["metrics"]["net_exposure_avg"],
            "cost_total": bt["metrics"]["cost_total"],
            "cost_pct_of_capital": bt["metrics"]["cost_pct_of_capital"],
            "trades": len(bt["trades"]),
        },
        "synthetic": {
            "evaluated": st["evaluated"],
            "candidate_count": st["candidate_count"],
            "confirmed_count": st["failure_count"],
            "failure_rate": st["failure_rate"],
            "mechanisms_evaluated": st["mechanisms_evaluated"],
            "worlds_per_mechanism": st["worlds_per_mechanism"],
            "untested_mechanisms": st["untested_mechanisms"],
            "failed_mechanisms": sorted({f["mechanism"] for f in st["confirmed_failures"]}),
        },
        "minimized": minimized,
        "adjacent_pass": adjacent,
        "limitations": [
            "Synthetic stress worlds are generated, not historical; they probe fragility, not real future risk.",
            "Costs are explicit bounded heuristics (flat bps), not validated broker calibrations.",
            "Fenrix fundamentals (if used) are not point-in-time; lagged approximation only.",
            "No claim of live profitability, production execution fidelity, or universal realism.",
            "Failure search is not exhaustive; it reports confirmed failures only.",
        ],
    }
    (base / "pitch" / "deck_data.json").write_text(json.dumps(deck_data, indent=2, default=str))

    # ---- manifest (binds strategy hash + claims digest) ----
    manifest = {
        "schema_version": "fenrix-submission/1.0",
        "git_sha": sha,
        "strategy_hash": run.strategy_hash,
        "data_manifest_hash": _whash(source_manifest),
        "claims_manifest_hash": claims_digest,
        "backtest_id": bt["backtest_id"],
        "campaign_id": st["strategy_hash"],
        "replay_id": run.strategy_hash,
        "verification": {
            "make_verify": "pending",
            "docker_smoke": "pending",
            "verify_submission": "pending",
        },
        "artifact_hashes": {
            "equity_curve.csv": _whash(equity_csv),
            "metrics.json": _whash(bt["metrics"]),
            "regime_matrix.csv": _whash("\n".join(regime_rows)),
            "deck_data.json": _whash(deck_data),
            "CLAIMS_MANIFEST.json": claims_digest,
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (base / "submission_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    (base / "verification.json").write_text(json.dumps(manifest["verification"], indent=2))

    return {
        "base_dir": str(base),
        "manifest": manifest,
        "deck_data": deck_data,
        "strategy_hash": run.strategy_hash,
        "failure_count": st["failure_count"],
    }
