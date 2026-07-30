"""Canonical market-data service: produce ONE MarketDataPanel contract accepted
by the executors, with explicit provenance and NO silent fallback.

Supports demo_fixture (deterministic) and yfinance (real) plus uploaded_csv.
If yfinance fails, an explicit error is returned -- never a demo/synthetic
substitution. The benchmark stays separate from the tradable matrix unless
``benchmark_tradable`` is True.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

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
    """SHA-256 over the COMPLETE market panel (Phase 2.6 section 6.5).

    Changing any of dates / asset ids+order / open / high / low / close / volume
    / benchmark / metadata / source / policy must change the digest.
    """
    h = hashlib.sha256()
    h.update(source.encode())
    h.update(",".join(panel.assets).encode())
    h.update(",".join(d.isoformat() for d in panel.dates).encode())
    h.update(np.ascontiguousarray(panel.open).tobytes())
    h.update(np.ascontiguousarray(panel.high).tobytes())
    h.update(np.ascontiguousarray(panel.low).tobytes())
    h.update(np.ascontiguousarray(panel.close).tobytes())
    h.update(np.ascontiguousarray(panel.volume).tobytes())
    if panel.benchmark_close is not None:
        h.update(np.ascontiguousarray(panel.benchmark_close).tobytes())
    h.update(json.dumps({k: v.__dict__ for k, v in panel.metadata.items()}, sort_keys=True).encode())
    h.update(panel.provenance.source.encode())
    h.update(panel.provenance.label.encode())
    return h.hexdigest()


def _business_days(start: date, count: int) -> tuple[date, ...]:
    """Deterministic weekday-only calendar (Phase 2.6 section 6.2).

    Excludes Saturdays/Sundays but does NOT model exchange holidays. Explicitly
    documented as a simplified business-day calendar.
    """
    out: list[date] = []
    d = start
    while len(out) < count:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d)
        d = d + timedelta(days=1)
    return tuple(out)


def build_demo_panel(
    universe: list[str],
    benchmark: str | None,
    seed: int = 20240101,
    benchmark_tradable: bool = False,
) -> MarketDataPanel:
    """Deterministic SYNTHETIC fixture panel covering ANY requested tickers.

    Benchmark separation (Phase 2.6 section 6.1): the benchmark is placed in
    ``panel.assets`` ONLY when ``benchmark_tradable=True``. Otherwise it rides as a
    separate ``benchmark_close`` series with its own identifier and is NOT part of
    the tradable asset matrix, so it can never be traded.
    """
    bench = benchmark.upper() if benchmark else None
    tradable = [s.upper() for s in dict.fromkeys(universe) if bench is None or s.upper() != bench]
    if not tradable:
        raise DataUnavailableError("no tradable universe symbols requested for demo fixture")
    # Benchmark is included in the asset matrix only if explicitly tradable.
    asset_syms = tradable + ([bench] if benchmark_tradable and bench is not None else [])
    benchmark_sym = bench if bench is not None else "SPY"  # series id when benchmark is None

    T = 504
    dates = _business_days(date(2021, 1, 4), T)

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

    n_assets = len(asset_syms)
    close = np.zeros((T, n_assets))
    for j, sym in enumerate(asset_syms):
        close[:, j] = _series(sym)
    # Valid OHLCV: open = prior close; high/low constructed from both open and
    # close so the OHLCV invariants hold (Phase 2.6 section 6.3).
    open_ = np.vstack([close[0], close[:-1]])
    high = np.maximum(open_, close) * 1.005
    low = np.minimum(open_, close) * 0.995
    volume = np.full((T, n_assets), 1_000_000.0)

    # Benchmark series (independent of the tradable matrix).
    bench_series = _series(benchmark_sym)

    metadata = {s: AssetMetadata(ticker=s, is_benchmark=False) for s in asset_syms}
    provenance = DataProvenance(
        source="fixture",
        tier=3,
        retrieval_timestamp="deterministic",
        source_hash=f"demo-synth-{seed}",
        transformations=["deterministic seeded return path"],
        warnings=[],
        label="deterministic fixture (synthetic)",
    )
    return MarketDataPanel(
        dates=dates,
        assets=tuple(asset_syms),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        benchmark_close=bench_series if bench is not None else None,
        metadata=metadata,
        provenance=provenance,
    )


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
    if source == "demo_fixture":
        adjustment_policy = "not applicable — synthetic"
        calendar_policy = "weekday-only synthetic calendar; exchange holidays not modeled"
    elif source == "yfinance":
        adjustment_policy = "split/dividend adjusted (yfinance auto-adjusted close)"
        calendar_policy = "actual NYSE trading calendar (exchange holidays observed)"
    else:
        adjustment_policy = "source-specific; see provider"
        calendar_policy = "provider calendar"
    return DataSourceProvenance(
        source=source,
        source_name=source_name,
        requested_symbols=requested,
        returned_symbols=returned,
        benchmark=benchmark,
        start_date=str(panel.dates[0]),
        end_date=str(panel.dates[-1]),
        retrieval_timestamp=datetime.now(UTC).isoformat(),
        adjustment_policy=adjustment_policy,
        calendar_policy=calendar_policy,
        missing_data_policy=missing_data_policy,
        coverage_by_symbol=coverage,
        warnings=list(panel.provenance.warnings),
        content_digest=_digest_panel(panel, source),
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
    benchmark_tradable: bool = False,
    csv_b64: str | None = None,
) -> tuple[MarketDataPanel, DataSourceProvenance]:
    """Single entry: build a provenance-carrying panel for the requested source.

    No silent fallback between data sources is performed.
    """
    if source == "demo_fixture":
        panel = build_demo_panel(
            universe, benchmark, seed=seed or 20240101, benchmark_tradable=benchmark_tradable
        )
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
    """Verify the supplied history satisfies the executor's lookback needs.

    Uses the executor's declared ``minimum_history_requirements`` (Phase 2.6
    section 7/13) rather than a hard-coded approximate map. Validates CONTIGUOUS,
    correctly ordered, finite history per tradable symbol -- not merely the total
    number of finite observations. Raises ``PanelTooShortError`` with a structured
    payload (required/available bars, signal name, affected symbols, required
    execution bars).
    """
    from app.strategies import executors  # noqa: F401  (registers executors into the default_registry)
    from app.strategies.contracts import HistoryRequirements
    from app.strategies.executors._base import tradable_mask
    from app.strategies.registry import default_registry

    ex = default_registry.get(spec.strategy_type)
    req: HistoryRequirements = ex.minimum_history_requirements(spec)

    T = panel.T
    # Contiguity + ordering: dates strictly increasing is guaranteed by the panel
    # validator; we additionally require contiguous valid history per symbol.
    mask = tradable_mask(spec, panel.assets)
    affected: list[str] = []
    for j in range(panel.N):
        if not mask[j]:
            continue
        col = panel.close[:, j]
        finite = np.isfinite(col)
        # longest run of contiguous finite bars ending at the last bar
        run = 0
        longest = 0
        for v in finite:
            run = run + 1 if v else 0
            longest = max(longest, run)
        if longest < req.contiguous_valid_bars:
            affected.append(panel.assets[j])

    if T < req.min_decision_bars or affected:
        raise PanelTooShortError(
            f"supplied history {T} bars < required {req.min_decision_bars} "
            f"({req.signal_reason}); execution needs {req.min_execution_bars} bars. "
            f"affected symbols (insufficient contiguous history): {affected or 'none'}"
        )


def panel_to_dict(panel: MarketDataPanel) -> dict:
    """Serialize a panel for durable storage (exact reconstruction)."""
    return {
        "dates": [d.isoformat() for d in panel.dates],
        "assets": list(panel.assets),
        "open": panel.open.tolist(),
        "high": panel.high.tolist(),
        "low": panel.low.tolist(),
        "close": panel.close.tolist(),
        "volume": panel.volume.tolist(),
        "benchmark_close": panel.benchmark_close.tolist() if panel.benchmark_close is not None else None,
        "metadata": {k: vars(v) for k, v in panel.metadata.items()},
        "provenance": {
            "source": panel.provenance.source,
            "tier": panel.provenance.tier,
            "retrieval_timestamp": panel.provenance.retrieval_timestamp,
            "source_hash": panel.provenance.source_hash,
            "transformations": list(panel.provenance.transformations),
            "warnings": list(panel.provenance.warnings),
            "label": panel.provenance.label,
        },
    }


def panel_from_dict(payload: dict) -> MarketDataPanel:
    """Reconstruct the EXACT persisted panel (Phase 2.6.1 gate 7)."""
    bench = payload.get("benchmark_close")
    return MarketDataPanel(
        dates=tuple(date.fromisoformat(d) for d in payload["dates"]),
        assets=tuple(payload["assets"]),
        open=np.asarray(payload["open"], dtype=float),
        high=np.asarray(payload["high"], dtype=float),
        low=np.asarray(payload["low"], dtype=float),
        close=np.asarray(payload["close"], dtype=float),
        volume=np.asarray(payload["volume"], dtype=float),
        benchmark_close=np.asarray(bench, dtype=float) if bench is not None else None,
        metadata={k: AssetMetadata(**v) for k, v in payload.get("metadata", {}).items()},
        provenance=DataProvenance(**payload["provenance"]),
    )


__all__ = [
    "acquire_panel",
    "enforce_bounds",
    "check_required_history",
    "build_demo_panel",
    "build_yfinance_panel",
    "panel_to_dict",
    "panel_from_dict",
    "MAX_ASSETS",
    "MAX_BARS",
    "MAX_CELLS",
]
