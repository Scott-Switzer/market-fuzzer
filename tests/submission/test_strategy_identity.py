"""Strategy identity tests: the canonical hash is the invariant binding every stage."""

from app.strategy_lab.submission.orchestrator import (
    build_strategy_dsl,
    lock_strategy,
    run_submission,
)
from app.strategy_lab.submission.strategy import CrossSectionalSpec


def test_hash_invariant_across_approval_backtest_stress():
    spec = CrossSectionalSpec()
    run = run_submission(spec=spec, mode="synthetic_fixture", budget=12)
    # approval id == strategy hash
    assert run.approval["strategy_id"] == run.strategy_hash
    # backtest carries same hash
    assert run.backtest["strategy_hash"] == run.strategy_hash
    # stress search carries same hash
    assert run.stress["strategy_hash"] == run.strategy_hash


def test_same_spec_same_hash_deterministic():
    h1 = lock_strategy(build_strategy_dsl(CrossSectionalSpec()))["strategy_id"]
    h2 = lock_strategy(build_strategy_dsl(CrossSectionalSpec()))["strategy_id"]
    assert h1 == h2


def test_changed_param_changes_hash():
    base = build_strategy_dsl(CrossSectionalSpec())
    h_base = lock_strategy(base)["strategy_id"]
    modified = build_strategy_dsl(CrossSectionalSpec(commission_bps=99.0))
    h_mod = lock_strategy(modified)["strategy_id"]
    assert h_base != h_mod
