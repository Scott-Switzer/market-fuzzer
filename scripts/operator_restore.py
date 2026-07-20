"""Restore an integrity-checked operator backup while the appliance is stopped."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.operator_backup import restore_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(restore_database(args.backup, args.manifest, args.destination, force=args.force), indent=2)
    )


if __name__ == "__main__":
    main()
