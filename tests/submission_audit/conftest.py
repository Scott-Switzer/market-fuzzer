"""Make _audit_helpers importable regardless of pytest rootdir/import mode."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
