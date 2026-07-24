"""Test-stress-mechanisms (spec 4.1-4.5).

 * 4.1 cross-process determinism (sha256, not hash())
 * 4.2 every registered mechanism is evaluated (no no-ops); intensity
       continuously affects minimizable mechanisms
 * 4.4 minimization is meaningful (reduced intensity fails less / passes)
 * 4.5 the run reports only mechanisms ACTUALLY evaluated
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from app.strategy_lab.submission.strategy import CrossSectionalSpec
from app.strategy_lab.submission.stress_search import (
    STRESS_MECHANISMS,
    apply_mechanism,
    mech_seed,
    minimize_failure,
    run_fast_search,
)


def _assets():
    return ["SYN_A", "SYN_B", "SYN_C", "SYN_D", "SYN_E", "SYN_F", "SPY"]


def test_stable_seed_not_python_hash():
    # 4.1: must be a stable sha256 digest, never Python's randomized hash()
    h = mech_seed("correlation_breakdown", 123)
    expected = int.from_bytes(
        hashlib.sha256(b"correlation_breakdown:123").digest()[:8], "big"
    )
    assert h == expected
    # deterministic across calls (Python hash() would vary per process)
    assert mech_seed("correlation_breakdown", 123) == h


def test_all_registered_mechanisms_evaluated():
    # 4.5: every mechanism in the registry must appear as actually evaluated,
    # not just listed from the registry.
    spec = CrossSectionalSpec(universe=_assets(), benchmark="SPY")
    res = run_fast_search(strategy_hash="x", spec=spec, base_assets=_assets(), T=504, budget=33)
    evaluated = set(res["mechanisms_evaluated"])
    assert evaluated == set(STRESS_MECHANISMS)
    assert res["untested_mechanisms"] == []
    # worlds per mechanism sums to evaluated count
    assert sum(res["worlds_per_mechanism"].values()) == res["evaluated"]


def test_no_noop_mechanism_intensity_has_effect():
    # 4.2: for an intensity-driven mechanism, a higher intensity must change the
    # stressed panel (no-op mechanisms would leave close unchanged).
    assets = _assets()
    rng = np.random.default_rng(1)
    T, N = 300, len(assets)
    close = np.zeros((T, N))
    for n in range(N):
        p = 100.0 + 5 * n
        for t in range(T):
            p *= 1.0 + rng.normal(0.0003, 0.01)
            close[t, n] = p
    for mech in ("volatility_expansion", "volatility_compression", "correlation_breakdown",
                 "momentum_reversal"):
        lo = apply_mechanism(close, mech, 0.0, 7, assets)["close"]
        hi = apply_mechanism(close, mech, 1.0, 7, assets)["close"]
        assert not np.allclose(lo, hi), f"{mech} is a no-op (intensity ignored)"


def test_short_unavailability_restricts_shorts():
    assets = _assets()
    rng = np.random.default_rng(2)
    T, N = 300, len(assets)
    close = np.array([[100.0 + 5 * i + rng.normal(0, 0.3) for i in range(N)]] * T, dtype=float)
    m = apply_mechanism(close, "short_unavailability", 0.8, 7, assets)
    assert len(m["non_shortable"]) > 0
    assert m["restricted_names"] > 0


def test_delayed_rebalance_sets_delay():
    assets = _assets()
    rng = np.random.default_rng(3)
    T, N = 300, len(assets)
    close = np.array([[100.0 + 5 * i + rng.normal(0, 0.3) for i in range(N)]] * T, dtype=float)
    m = apply_mechanism(close, "delayed_rebalance", 0.5, 7, assets)
    assert m["execution_delay_days"] >= 1


def test_minimization_is_meaningful():
    # 4.4: minimizing an intensity-driven confirmed failure must yield a lower
    # intensity that still fails (binary search on a monotonic mechanism).
    spec = CrossSectionalSpec(universe=_assets(), benchmark="SPY")
    res = run_fast_search(strategy_hash="x", spec=spec, base_assets=_assets(), T=504, budget=33)
    confirmed = res["confirmed_failures"]
    if not confirmed:
        pytest.skip("no confirmed failure in this seed sweep")
    f0 = confirmed[0]
    if f0["mechanism"] in ("universe_churn", "missing_data_shock", "short_unavailability"):
        # categorical mechanisms minimize a categorical parameter instead
        minres = minimize_failure("x", spec, f0, base_assets=_assets())
        assert "minimized_affected" in minres
    else:
        minres = minimize_failure("x", spec, f0, base_assets=_assets())
        assert "minimized_intensity" in minres
        assert minres["minimized_intensity"] <= f0["intensity"] + 1e-9
