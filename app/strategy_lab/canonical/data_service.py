"""Canonical market-data service: produce ONE MarketDataPanel contract accepted
by the executors, with explicit provenance and NO silent fallback.

Supports demo_fixture (deterministic) and yfinance (real) plus uploaded_csv.
If yfinance fails, an explicit error is returned -- never a demo/synthetic
substitution. The benchmark stays separate from the tradable matrix unless
``benchmark_tradable`` is True.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import numpy as np

from app.domain.strategy_spec import StrategySpec
from app.strategy_lab.canonical.contracts import DataSourceProvenance
from app.strategy_lab.canonical.errors import DataUnavailableError, PanelTooShortError
from app.strategy_lab.submission.panels import (
    AssetMetadata,
    DataProvenance,
    MarketDataPanel,
)

# Bounded synchronous-execution limits (Phase 2.5 section 6.5).
MAX_ASSETS = 100
MAX_BARS = 10_000
MAX_CELLS = 500_000


def _digest_panel(panel: MarketDataPanel, source: str) -> str:
    h = hashlib.sha256()
    h.update(source.encode())
    h.update(",".join(panel.assets).encode())
    h.update(str(len(panel.dates)).encode())
    h.update(np.ascontiguousarray(panel.close).tobytes())
    return h.hexdigest()


def _panel_provenance(
    panel: MarketDataPanel,
    source: str,
    source_name: str,
    requested: list[str],
    benchmark: str | None,
    missing_data_policy: str,
) -> DataSourceProvenance:
    returned = list(panel.assets)
    coverage: dict[str, float] = {}
    for a in returned:
        col = panel.close[:, panel.asset_index(a)]
        valid = float(np.sum(np.isfinite(col)))
        coverage[a] = round(valid / max(1, len(col)), 4)
    return DataSourceProvenance(
        source=source,
        source_name=source_name,
        requested_symbols=requested,
        returned_symbols=returned,
        benchmark=benchmark,
        start_date=str(panel.dates[0]),
        end_date=str(panel.dates[-1]),
        retrieval_timestamp=datetime.now(UTC).isoformat(),
        adjustment_policy="split/dividend adjusted (best-effort)",
        calendar_policy="actual trading calendar; synthetic/relative dates flagged",
        missing_data_policy=missing_data_policy,
        coverage_by_symbol=coverage,
        warnings=list(panel.provenance.warnings),
        content_digest=_digest_panel(panel, source),
    )


def build_demo_panel(universe: list[str], benchmark: str | None, seed: int = 20240101) -> MarketDataPanel:
    """Deterministic SYNTHETIC fixture panel covering ANY requested tickers.

    The demo source is explicitly labeled synthetic: it generates reproducible
    OHLCV series (per-symbol deterministic drift/vol seeded by the symbol name)
    for exactly the requested tradable universe plus the benchmark. The
    benchmark column is provided separately and excluded from the tradable
    matrix so it cannot be accidentally traded.
    """
    bench = (benchmark or "SPY").upper()
    tradable = [s.upper() for s in dict.fromkeys(universe) if s.upper() != bench]
    if not tradable:
        raise DataUnavailableError("no tradable universe symbols requested for demo fixture")
    all_syms = tradable + [bench]

    T = 504
    from datetime import date, timedelta

    dates = tuple(date(2021, 1, 1) + timedelta(days=i) for i in range(T))

    def _symseed(sym: str) -> int:
        return int(hashlib.sha256(sym.encode()).hexdigest()[:8], 16)

    def _series(sym: str) -> np.ndarray:
        h = _symseed(sym)
        rng = np.random.default_rng(seed ^ h)
        drift = 0.0002 + (h % 7) * 0.00005
        vol = 0.008 + (h % 5) * 0.002
        base = 50.0 + (h % 150)
        prices = np.empty(T, dtype=float)
        p = base
        for t in range(T):
            d = drift
            # mid-sample reversal to give the failure lab something to find
            if 220 <= t < 300:
                d = -drift * 1.5
            p *= 1.0 + d + rng.normal(0.0, vol)
            prices[t] = max(p, 1.0)
        return prices

    n = len(all_syms)
    close = np.zeros((T, n))
    for j, sym in enumerate(all_syms):
        close[:, j] = _series(sym)
    open_ = np.vstack([close[0], close[:-1]])  # next-open ~ prior close
    high = close * 1.005
    low = close * 0.995
    volume = np.full((T, n), 1_000_000.0)

    metadata = {s: AssetMetadata(ticker=s, is_benchmark=(s == bench)) for s in all_syms}
    provenance = DataProvenance(
        source="fixture",
        tier=3,
        retrieval_timestamp="deterministic",
        source_hash=f"demo-synth-{seed}",
    )
    return MarketDataPanel(
        dates=dates,
        assets=tuple(all_syms),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        benchmark_close=close[:, all_syms.index(bench)],
        metadata=metadata,
        provenance=provenance,
    )


def build_yfinance_panel(
    universe: list[str], benchmark: str | None, start: str | None, end: str | None
) -> MarketDataPanel:
    """Acquire a real yfinance panel. Raises DataUnavailableError on failure --
    never silently falls back to demo data."""
    from app.strategy_lab.submission.yfinance_adapter import acquire

    bench = benchmark or "SPY"
    symbols = list(dict.fromkeys(list(universe) + [bench]))
    start = start or "2018-01-01"
    end = end or "2023-12-31"
    try:
        result = acquire(tickers=symbols, start=start, end=end)
    except Exception as exc:  # explicit: surface the failure, do not substitute
        raise DataUnavailableError(
            f"yfinance acquisition failed for {symbols}: {exc}. No silent fallback used."
        ) from exc
    if "panel" not in result:
        reason = result.get("error", "unknown acquisition error")
        raise DataUnavailableError(
            f"yfinance acquisition failed for {symbols}: {reason}. No silent fallback used."
        )
    return result["panel"]


def acquire_panel(
    source: str,
    universe: list[str],
    benchmark: str | None,
    *,
    start: str | None = None,
    end: str | None = None,
    seed: int | None = None,
    csv_b64: str | None = None,
) -> tuple[MarketDataPanel, DataSourceProvenance]:
    """Single entry: build a provenance-carrying panel for the requested source.

    No silent fallback between data sources is performed.
    """
    if source == "demo_fixture":
        panel = build_demo_panel(universe, benchmark, seed=seed or 20240101)
        prov = _panel_provenance(
            panel, source, "deterministic fixture", universe, benchmark, "none (synthetic)"
        )
        return panel, prov
    if source == "yfinance":
        panel = build_yfinance_panel(universe, benchmark, start, end)
        prov = _panel_provenance(
            panel,
            source,
            "yfinance (research/educational)",
            universe,
            benchmark,
            "forward-fill flagged; survivorship-limited",
        )
        return panel, prov
    if source == "uploaded_csv":
        raise DataUnavailableError("uploaded_csv ingestion is not yet wired in this phase")
    raise DataUnavailableError(f"unsupported data source: {source}")


def enforce_bounds(panel: MarketDataPanel) -> None:
    """Reject requests exceeding central bounded-execution limits (section 6.5)."""
    from app.strategy_lab.canonical.errors import BoundedExecutionLimitError

    T, N = panel.T, panel.N
    if N > MAX_ASSETS:
        raise BoundedExecutionLimitError(f"assets {N} > max {MAX_ASSETS}")
    if T > MAX_BARS:
        raise BoundedExecutionLimitError(f"bars {T} > max {MAX_BARS}")
    if T * N > MAX_CELLS:
        raise BoundedExecutionLimitError(f"panel cells {T * N} > max {MAX_CELLS}")


def check_required_history(spec: StrategySpec, panel: MarketDataPanel) -> None:
    """Verify the supplied history satisfies the executor's lookback needs."""
    from app.strategies.executors._base import tradable_mask

    T = panel.T
    # Map strategy type -> minimum bar requirement.
    req: dict[str, int] = {
        "cross_sectional_factor": 252,
        "long_only_ranking": 252,
        "time_series_signal": 60,
        "tactical_allocation": 252,
        "static_allocation": 2,
    }
    needed = req.get(spec.strategy_type.value, 60)
    if spec.strategy_type.value == "time_series_signal":
        for s in spec.signal_definitions:
            slow = getattr(s, "slow_window", None)
            if getattr(s, "kind", "") == "sma" and slow is not None:
                needed = max(needed, int(slow) + 2)
    if T < needed:
        raise PanelTooShortError(
            f"supplied history {T} bars < required {needed} for {spec.strategy_type.value}"
        )
    # Tradable coverage check (no shortening of lookbacks).
    mask = tradable_mask(spec, panel.assets)
    for j in range(panel.N):
        if mask[j] and np.sum(np.isfinite(panel.close[:, j])) < needed:
            raise PanelTooShortError(
                f"symbol {panel.assets[j]} has insufficient history for lookback {needed}"
            )


__all__ = [
    "acquire_panel",
    "enforce_bounds",
    "check_required_history",
    "build_demo_panel",
    "build_yfinance_panel",
    "MAX_ASSETS",
    "MAX_BARS",
    "MAX_CELLS",
]
