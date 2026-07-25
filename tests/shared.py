from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest

_FENRIX_ZIP = Path("~/Documents/scott-brain/22_Fenrix/anonymized_bundle.zip").expanduser()


def require_fenrix_zip() -> None:
    if not _FENRIX_ZIP.exists():
        pytest.skip(f"Fenrix zip missing: {_FENRIX_ZIP}")


def make_temp_fenrix_zip(tmp_path: Path) -> Path:
    archive = tmp_path / "fenrix_bundle.zip"
    with zipfile.ZipFile(archive, mode="w") as bundle:
        for company_id in ("COMPANY_001", "COMPANY_002"):
            closes = [
                float(value)
                for value in (
                    100 * np.exp(np.cumsum(np.random.default_rng(7).normal(0.0003, 0.01, 120)))
                ).tolist()
            ]
            bundle.writestr(
                f"public/anonymized/{company_id}/market/price_series.csv",
                "step,close\n" + "\n".join(f"{idx},{price:.6f}" for idx, price in enumerate(closes, start=1)),
            )
    return archive
