#!/usr/bin/env python
"""Build the Fenrix submission deck (HTML + PPTX + PDF) from real evidence.

Usage:
    env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv312/bin/python scripts/build_submission_deck.py
    ... [--allow-stale]   # fall back to latest evidence if current SHA has none

Fails loudly if evidence for the current git SHA is missing (unless --allow-stale).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_submission_deck")
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="allow building from latest evidence even if it is not the current git SHA",
    )
    args = parser.parse_args(argv)

    import os

    os.chdir(REPO_ROOT)

    from app.strategy_lab.submission.deck import build_deck_all

    outputs = build_deck_all(require_current_sha=not args.allow_stale)
    print("SUBMISSION DECK BUILT (all formats, evidence-driven)")
    for k, v in outputs.items():
        print(f"  {k:9s}: {v}")

    # sanity: all outputs exist and are non-trivial
    for key in ("html", "pptx", "pdf"):
        p = Path(outputs[key])
        if not p.exists() or p.stat().st_size < 1000:
            print(f"ERROR: output {key} missing or too small: {p}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
