"""Thin CLI for data inspection (Tier 1 Fenrix bundle)."""

from __future__ import annotations

import argparse
import sys

from app.strategy_lab.submission.fenrix_adapter import inspect_fenrix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data")
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("inspect-fenrix")
    f.add_argument("--path", default=None)
    args = parser.parse_args(argv)
    if args.command == "inspect-fenrix":
        inv = inspect_fenrix(args.path)
        print(json.dumps(inv, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    import json

    sys.exit(main())
