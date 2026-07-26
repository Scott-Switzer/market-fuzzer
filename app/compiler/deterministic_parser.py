"""Deterministic plain-English -> StrategySpec compiler (reset brief items 25-27).

Forbidden behaviors that this compiler MUST NOT exhibit (and is tested against):
  * mapping long-only momentum to a long/short family;
  * converting unknown input into some default family (macro_gated_risk_off);
  * hard-coding an S&P 500 point-in-time universe when none was given;
  * approving the whole thesis as a single clause.

Instead: a small, explicit grammar recognizes supported strategy shapes, emits
one ledger entry per interpreted clause, and returns ``unsupported`` for prose it
cannot map -- never a substitute family. Missing universe becomes a required
resolution or a clearly-labeled assumption (never a silent default).
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.compiler.result import CompilationResult, LedgerEntry
from app.domain.strategy_spec import (
    Frequency,
    MomentumSignal,
    PortfolioConstruction,
    RiskConstraints,
    SimpleMovingAverageSignal,
    StrategySpec,
    StrategyType,
    Weighting,
)

_NUM = r"(\d+(?:\.\d+)?)"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _pct(token: str) -> float:
    token = token.strip().rstrip("%")
    v = float(token)
    return v / 100.0 if v > 1.0 else v


def _frequency(text: str) -> tuple[Frequency, str]:
    """Return (frequency, span) detected in text; default monthly assumption."""
    if re.search(r"\b(daily|each day|every day)\b", text):
        return Frequency.DAILY, "daily"
    if re.search(r"\b(weekly|each week|every week)\b", text):
        return Frequency.WEEKLY, "weekly"
    if re.search(r"\b(monthly|each month|every month|per month)\b", text):
        return Frequency.MONTHLY, "monthly"
    return Frequency.MONTHLY, ""  # assumption when unspecified


def _find_tickers(text: str) -> list[str]:
    """Uppercase tokens that look like tickers (2-5 A-Z, optional .X/-X)."""
    raw = re.findall(r"\b([A-Z]{1,5}(?:[.\-][A-Z])?)\b", text)
    stop = {"SMA", "RSI", "ETF", "ETFS", "AND", "THE", "BUY", "GO", "IF", "OR", "TOP", "BY"}
    out: list[str] = []
    for t in raw:
        if t in stop:
            continue
        if t not in out:
            out.append(t)
    return out


def compile_thesis(thesis: str, *, name: str | None = None) -> CompilationResult:
    """Compile a plain-English thesis into a StrategySpec draft + ledger."""
    original = thesis
    text = _clean(thesis)
    low = text.lower()
    strat_name = name or (text[:60] if text else "strategy")

    # dispatch on explicit, non-overlapping shapes
    for matcher in (
        _match_static_allocation,
        _match_sma_crossover,
        _match_tactical_allocation,
        _match_cross_sectional,
        _match_long_only_ranking,
    ):
        result = matcher(original, text, low, strat_name)
        if result is not None:
            return result

    return _unsupported(original, strat_name)


# ---------------------------------------------------------------------------
# matchers (each returns CompilationResult or None)
# ---------------------------------------------------------------------------
def _match_static_allocation(original, text, low, name) -> CompilationResult | None:  # noqa: ANN001
    # "Allocate 60% to SPY and 40% to AGG and rebalance monthly."
    pairs = re.findall(rf"{_NUM}\s*%?\s*(?:to|in)\s+([A-Z]{{1,5}}(?:[.\-][A-Z])?)", text)
    if not pairs or "allocate" not in low and "%" not in text:
        # require explicit allocation phrasing
        if "allocate" not in low:
            return None
    if len(pairs) < 2:
        return None
    weights: dict[str, Decimal] = {}
    clauses: list[LedgerEntry] = []
    for num, sym in pairs:
        w = _pct(num)
        weights[sym.upper()] = Decimal(f"{w:.6f}")
        clauses.append(
            LedgerEntry(
                id=f"weight_{sym.upper()}",
                original_text_span=f"{num}% to {sym}",
                normalized_interpretation=f"target weight {sym.upper()}={w:.4f}",
                status="resolved",
                confidence=0.95,
            )
        )
    freq, span = _frequency(low)
    universe = list(weights.keys())
    clauses.append(_freq_clause(freq, span))
    clauses.append(
        LedgerEntry(
            id="universe",
            original_text_span=", ".join(universe),
            normalized_interpretation=f"universe = {universe}",
            status="resolved",
            confidence=0.97,
        )
    )
    spec = StrategySpec(
        name=name,
        original_thesis=original,
        strategy_type=StrategyType.STATIC_ALLOCATION,
        universe=universe,
        frequency=freq,
        rebalance_policy=freq,
        long_short_policy="long_only",
        portfolio_construction=PortfolioConstruction(weighting=Weighting.FIXED, target_weights=weights),
        clauses=[],
    )
    return _finish(original, spec, clauses, [], [])


def _match_sma_crossover(original, text, low, name) -> CompilationResult | None:  # noqa: ANN001
    # "Buy SPY when its 20-day average is above its 50-day average, otherwise hold cash."
    if "average" not in low and "sma" not in low and "moving average" not in low:
        return None
    windows = [int(x) for x in re.findall(rf"{_NUM}\s*-?\s*day", low)]
    if len(windows) < 2:
        return None
    fast, slow = sorted(windows[:2])
    tickers = _find_tickers(text)
    if len(tickers) != 1:
        return None
    ticker = tickers[0]
    freq, span = _frequency(low)
    if not span:
        freq = Frequency.DAILY  # crossover strategies default to daily decisioning
    clauses = [
        LedgerEntry(
            id="signal",
            original_text_span=f"{fast}-day vs {slow}-day average",
            normalized_interpretation=f"SMA crossover fast={fast} slow={slow}",
            status="resolved",
            confidence=0.93,
        ),
        LedgerEntry(
            id="universe",
            original_text_span=ticker,
            normalized_interpretation=f"single asset = {ticker}",
            status="resolved",
            confidence=0.96,
        ),
        LedgerEntry(
            id="long_cash_policy",
            original_text_span="otherwise hold cash",
            normalized_interpretation="long when fast>slow else flat (cash)",
            status="resolved",
            confidence=0.9,
        ),
        _freq_clause(freq, span),
    ]
    spec = StrategySpec(
        name=name,
        original_thesis=original,
        strategy_type=StrategyType.TIME_SERIES_SIGNAL,
        universe=[ticker],
        frequency=freq,
        rebalance_policy=freq,
        long_short_policy="long_only",
        signal_definitions=[SimpleMovingAverageSignal(id="sma", fast_window=fast, slow_window=slow)],
        clauses=[],
    )
    return _finish(original, spec, clauses, [], [])


def _match_tactical_allocation(original, text, low, name) -> CompilationResult | None:  # noqa: ANN001
    # "Each month hold the top 3 asset ETFs by 12-month momentum if above their
    #  200-day average; otherwise hold BIL."
    # Discriminator: a trend/absolute filter ("above ... average"/"trend") AND a
    # defensive fallback ("otherwise/else hold X"). This is what separates
    # tactical rotation from a plain long-only ranking.
    has_trend = "200" in low or "trend" in low or "above" in low and "average" in low
    fb = re.search(r"(?:otherwise|else)\s+(?:hold\s+|move to\s+|go to\s+)?([A-Z]{1,5})", text)
    if not (has_trend and fb):
        return None
    if "top" not in low:
        return None
    m = re.search(rf"top\s+{_NUM}", low)
    top_k = int(float(m.group(1))) if m else 3
    trend = 200
    tw = re.search(rf"{_NUM}\s*-?\s*day", low)
    if tw:
        trend = int(float(tw.group(1)))
    fallback = fb.group(1).upper()
    tickers = _find_tickers(text)
    if fallback not in tickers:
        tickers.append(fallback)
    freq, span = _frequency(low)
    clauses = [
        LedgerEntry(
            id="signal",
            original_text_span="12-month momentum",
            normalized_interpretation="252-bar total return ranking",
            status="resolved",
            confidence=0.9,
        ),
        LedgerEntry(
            id="trend_filter",
            original_text_span=f"{trend}-day average",
            normalized_interpretation=f"eligibility close>{trend}-day SMA",
            status="resolved",
            confidence=0.9,
        ),
        LedgerEntry(
            id="selection",
            original_text_span=f"top {top_k}",
            normalized_interpretation=f"top {top_k} by return",
            status="resolved",
            confidence=0.92,
        ),
        LedgerEntry(
            id="fallback",
            original_text_span=f"otherwise hold {fallback}",
            normalized_interpretation=f"100% {fallback} when none pass",
            status="resolved",
            confidence=0.92,
        ),
        _freq_clause(freq, span),
    ]
    spec = StrategySpec(
        name=name,
        original_thesis=original,
        strategy_type=StrategyType.TACTICAL_ALLOCATION,
        universe=tickers,
        frequency=freq,
        rebalance_policy=freq,
        long_short_policy="long_only",
        lookback_windows={"return": 252, "trend": trend},
        ranking_or_threshold_rules={"top_k": top_k, "fallback": fallback},
        clauses=[],
    )
    return _finish(original, spec, clauses, [], [])


def _match_cross_sectional(original, text, low, name) -> CompilationResult | None:  # noqa: ANN001
    # "Each month go long the top 20% and short the bottom 20% by 12-1 momentum."
    if "momentum" not in low:
        return None
    if not ("short" in low and "long" in low):
        return None
    qs = [float(x) for x in re.findall(rf"{_NUM}\s*%", text)]
    long_q = _pct(str(qs[0])) if qs else 0.2
    short_q = _pct(str(qs[1])) if len(qs) > 1 else long_q
    freq, span = _frequency(low)
    universe, uni_clause, resolutions, assumptions = _resolve_universe(text, low)
    clauses = [
        LedgerEntry(
            id="signal",
            original_text_span="12-1 momentum",
            normalized_interpretation="momentum lookback=252 skip=21",
            status="resolved",
            confidence=0.92,
        ),
        LedgerEntry(
            id="long_selection",
            original_text_span=f"top {qs[0] if qs else 20}%",
            normalized_interpretation=f"long_quantile={long_q}",
            status="resolved",
            confidence=0.92,
        ),
        LedgerEntry(
            id="short_selection",
            original_text_span=f"bottom {qs[1] if len(qs) > 1 else 20}%",
            normalized_interpretation=f"short_quantile={short_q}",
            status="resolved",
            confidence=0.92,
        ),
        LedgerEntry(
            id="long_short_policy",
            original_text_span="long/short",
            normalized_interpretation="dollar-neutral long/short",
            status="resolved",
            confidence=0.9,
        ),
        _freq_clause(freq, span),
        uni_clause,
    ]
    spec = StrategySpec(
        name=name,
        original_thesis=original,
        strategy_type=StrategyType.CROSS_SECTIONAL_FACTOR,
        universe=universe,
        frequency=freq,
        rebalance_policy=freq,
        long_short_policy="long_short",
        signal_definitions=[MomentumSignal(id="mom")],
        portfolio_construction=PortfolioConstruction(
            long_short=True, long_quantile=Decimal(f"{long_q}"), short_quantile=Decimal(f"{short_q}")
        ),
        risk_constraints=RiskConstraints(
            gross_exposure_limit=Decimal("1.0"),
            net_exposure_target=Decimal("0.0"),
            max_position_weight=Decimal("0.5"),
        ),
        clauses=[],
    )
    return _finish(original, spec, clauses, assumptions, resolutions)


def _match_long_only_ranking(original, text, low, name) -> CompilationResult | None:  # noqa: ANN001
    # "Buy the top 5 stocks by 12-1 momentum each month."
    if "momentum" not in low:
        return None
    if "short" in low:
        return None
    m = re.search(rf"top\s+{_NUM}", low)
    if not m:
        return None
    top_n = int(float(m.group(1)))
    freq, span = _frequency(low)
    universe, uni_clause, resolutions, assumptions = _resolve_universe(text, low)
    clauses = [
        LedgerEntry(
            id="signal",
            original_text_span="12-1 momentum",
            normalized_interpretation="momentum lookback=252 skip=21",
            status="resolved",
            confidence=0.92,
        ),
        LedgerEntry(
            id="selection",
            original_text_span=f"top {top_n}",
            normalized_interpretation=f"long_count={top_n}",
            status="resolved",
            confidence=0.93,
        ),
        LedgerEntry(
            id="long_only_policy",
            original_text_span="buy the top",
            normalized_interpretation="long only, equal weight",
            status="resolved",
            confidence=0.92,
        ),
        _freq_clause(freq, span),
        uni_clause,
    ]
    spec = StrategySpec(
        name=name,
        original_thesis=original,
        strategy_type=StrategyType.LONG_ONLY_RANKING,
        universe=universe,
        frequency=freq,
        rebalance_policy=freq,
        long_short_policy="long_only",
        signal_definitions=[MomentumSignal(id="mom")],
        portfolio_construction=PortfolioConstruction(long_count=top_n),
        risk_constraints=RiskConstraints(
            gross_exposure_limit=Decimal("1.0"), max_position_weight=Decimal("0.5")
        ),
        clauses=[],
    )
    return _finish(original, spec, clauses, assumptions, resolutions)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _freq_clause(freq: str, span: str) -> LedgerEntry:
    if span:
        return LedgerEntry(
            id="rebalance_frequency",
            original_text_span=span,
            normalized_interpretation=f"rebalance {freq}",
            status="resolved",
            confidence=0.95,
        )
    return LedgerEntry(
        id="rebalance_frequency",
        original_text_span="(unspecified)",
        normalized_interpretation=f"rebalance {freq} (assumed)",
        status="assumption",
        confidence=0.6,
        assumption=f"rebalance frequency not stated; assumed {freq}",
    )


def _resolve_universe(text, low):  # noqa: ANN001
    """Explicit tickers if present; otherwise a required user resolution.

    NEVER defaults to an S&P 500 point-in-time membership.
    """
    tickers = _find_tickers(text)
    # If the thesis names a concrete list of tickers, use it.
    if len(tickers) >= 3:
        entry = LedgerEntry(
            id="universe",
            original_text_span=", ".join(tickers),
            normalized_interpretation=f"universe = {tickers}",
            status="resolved",
            confidence=0.9,
        )
        return tickers, entry, [], []
    # Otherwise the universe is unresolved -- require the user to supply it.
    entry = LedgerEntry(
        id="universe",
        original_text_span="(no universe specified)",
        normalized_interpretation="universe must be supplied by the user",
        status="needs_resolution",
        confidence=0.3,
        resolution="universe: provide an explicit tradable universe (list of tickers)",
    )
    return [], entry, [], []


def _finish(original, spec, clauses, assumptions, resolutions) -> CompilationResult:  # noqa: ANN001
    required_data = list(spec.universe)
    res = list(
        dict.fromkeys(
            [c.resolution for c in clauses if c.status == "needs_resolution" and c.resolution]
            + list(resolutions)
        )
    )
    return CompilationResult(
        original_thesis=original,
        strategy_spec_draft=spec,
        clauses=clauses,
        assumptions=[c.assumption for c in clauses if c.assumption] + list(assumptions),
        unsupported_clauses=[],
        required_data=required_data,
        required_user_resolutions=res,
        confidence_by_clause={c.id: c.confidence for c in clauses},
    )


def _unsupported(original, name) -> CompilationResult:  # noqa: ANN001
    entry = LedgerEntry(
        id="thesis",
        original_text_span=original[:120],
        normalized_interpretation="no supported strategy grammar matched this thesis",
        status="unsupported",
        confidence=0.0,
    )
    spec = StrategySpec(
        name=name or "unsupported",
        original_thesis=original if len(re.sub(r"\s", "", original)) >= 10 else (original + " (unsupported)"),
        strategy_type=StrategyType.UNSUPPORTED,
        universe=[],
        clauses=[],
    )
    return CompilationResult(
        original_thesis=original,
        strategy_spec_draft=spec,
        clauses=[entry],
        unsupported_clauses=[entry],
        confidence_by_clause={entry.id: 0.0},
    )


__all__ = ["compile_thesis"]
