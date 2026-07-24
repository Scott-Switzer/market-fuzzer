"""CLI entry for the Fenrix submission hardening.

Usage:
  python -m app.strategy_lab.submission.cli demo                  # offline fixture demo
  python -m app.strategy_lab.submission.cli demo --mode yfinance   # historical run of record
  python -m app.strategy_lab.submission.cli build-deck            # rebuild deck from evidence
"""

from __future__ import annotations

import argparse
import sys

from app.strategy_lab.submission.deck import build_deck
from app.strategy_lab.submission.evidence import build_evidence_package
from app.strategy_lab.submission.orchestrator import run_submission
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def cmd_demo(args: argparse.Namespace) -> int:
    mode = args.mode
    if mode == "historical":
        mode = "yfinance"
    spec = CrossSectionalSpec()
    if mode == "yfinance":
        # Historical run of record: require real data; do NOT silently fall back.
        from app.strategy_lab.submission.yfinance_adapter import acquire

        probe = acquire(
            tickers=list(spec.universe) + [spec.benchmark], start=spec.start, end=spec.end, use_cache=True
        )
        if probe.get("panel") is None:
            print("SUBMISSION DEMO (HISTORICAL) FAILED: no yfinance data/cache available.")
            print("  Refusing to overwrite the primary evidence deck with synthetic-fixture results.")
            return 2
    run = run_submission(spec=spec, mode=mode, use_cache=not args.no_cache, budget=args.budget)
    ev = build_evidence_package(run)
    print("SUBMISSION DEMO COMPLETE")
    print(f"  strategy_hash : {run.strategy_hash}")
    print(f"  data_mode     : {run.data_mode}")
    print(f"  equity_end    : {run.backtest['metrics']['final_equity']}")
    print(f"  sharpe        : {run.backtest['metrics']['sharpe']}")
    print(f"  failures      : {run.stress['failure_count']}/{run.stress['evaluated']}")
    print(f"  evidence_dir  : {ev['base_dir']}")
    return 0


def cmd_build_deck(args: argparse.Namespace) -> int:
    build_deck()
    print("PITCH DECK REBUILT from evidence")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="submission")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo")
    d.add_argument(
        "--mode",
        default="synthetic_fixture",
        choices=["synthetic_fixture", "yfinance", "fenrix", "auto", "historical", "offline"],
    )
    d.add_argument("--no-cache", action="store_true")
    d.add_argument("--budget", type=int, default=24)
    d.set_defaults(func=cmd_demo)
    b = sub.add_parser("build-deck")
    b.set_defaults(func=cmd_build_deck)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
