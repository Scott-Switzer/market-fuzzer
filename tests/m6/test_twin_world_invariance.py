"""M6.3 -- twin-world intervention test.

Intervene on one mechanism input in an otherwise identical world. Every
observable variable that is not a descendant of the intervention in the
registry DAG must be byte-identical between the two worlds. This works only
because World V2 draws from SEMANTIC_RNG_V3 addresses, so an intervention
cannot shift any unrelated random draw.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.causal import world_v2 as decl
from app.economy.v2 import EconomyEngineV2, EconomyParamsV2, InterventionV2
from tests.m6.twin import changed, flatten

REG = decl.build_world_v2_registry()
START = date(2027, 6, 30)
PARAMS = EconomyParamsV2(years=4, seed=20260921)
COMPANIES = 8

_CACHE: dict[object, object] = {}


def _world(interventions: tuple[InterventionV2, ...] = (), *, seed: int = PARAMS.seed):
    key = (interventions, seed)
    if key not in _CACHE:
        params = EconomyParamsV2(years=PARAMS.years, seed=seed)
        _CACHE[key] = EconomyEngineV2(params, interventions=interventions, company_count=COMPANIES).run()
    return _CACHE[key]


def _sector_map(outcome):
    return {f"COMPANY:{c['ticker']}": c["sector"] for c in outcome.companies}


def _nodes(variable: str, company_slot: int | None, outcome):
    """Return (intervention, registry target node) for a clamp on one company or the world."""
    # slot indexes the companies still reporting at START, so a defaulted company is never the target
    tickers = sorted({q.company for q in outcome.quarters if q.period_end >= START})
    if company_slot is None:
        value = 300.0
        return InterventionV2("WORLD", variable, value, START), (variable, decl.ENTITY_WORLD)
    ticker = tickers[company_slot]
    value = {"demand_index": 0.6, "fraud_propensity": 0.99, "supplier_failure": 1.0}[variable]
    return InterventionV2(ticker, variable, value, START), (variable, f"COMPANY:{ticker}")


CASES = [
    ("demand_index", 0),
    ("demand_index", 3),
    ("fraud_propensity", 1),
    ("supplier_failure", 2),
    ("rate_shock_bps", None),
]


@pytest.mark.parametrize(("variable", "slot"), CASES)
@pytest.mark.parametrize("seed", [20260921, 7])
def test_non_descendants_are_byte_identical(variable, slot, seed):
    base = _world(seed=seed)
    iv, target = _nodes(variable, slot, base)
    twin = _world((iv,), seed=seed)
    assert [c["ticker"] for c in twin.companies] == [c["ticker"] for c in base.companies]

    descendants = REG.descendants([target], sector_of_company=_sector_map(base))
    base_bytes, twin_bytes = flatten(base), flatten(twin)
    differing = changed(base_bytes, twin_bytes)

    # The invariant under test: nothing outside the DAG's descendant set moved.
    assert differing <= descendants, sorted(differing - descendants)[:10]

    # Non-vacuity: the intervention did something, and the descendant set is not everything.
    assert differing, "the intervention had no observable effect; the test would pass vacuously"
    untouched = set(base_bytes) - descendants
    assert untouched, "every variable is a descendant; the invariance claim is vacuous"
    assert all(base_bytes[k] == twin_bytes[k] for k in untouched)


@pytest.mark.parametrize(("variable", "slot"), CASES)
def test_company_interventions_never_change_another_company(variable, slot):
    if slot is None:
        pytest.skip("world-level interventions legitimately reach every company")
    base = _world()
    iv, target = _nodes(variable, slot, base)
    twin = _world((iv,))
    other = {k for k in changed(flatten(base), flatten(twin)) if k[1] not in {target[1], decl.ENTITY_WORLD}}
    assert other == set()


@pytest.mark.parametrize(("variable", "slot"), CASES)
def test_nothing_before_the_intervention_start_changes(variable, slot):
    base = _world()
    iv, _target = _nodes(variable, slot, base)
    twin = _world((iv,))
    early_base = flatten(base, before=START)
    early_twin = flatten(twin, before=START)
    assert changed(early_base, early_twin) == set(), "an intervention leaked into the past"


def test_macro_is_untouched_by_every_company_level_intervention():
    base = _world()
    for variable, slot in CASES:
        if slot is None:
            continue
        iv, _ = _nodes(variable, slot, base)
        twin = _world((iv,))
        fb, ft = flatten(base), flatten(twin)
        macro = {k for k in changed(fb, ft) if k[0].startswith("macro.")}
        assert macro == set()


def test_world_rate_shock_leaves_macro_real_variables_untouched():
    base = _world()
    iv, target = _nodes("rate_shock_bps", None, base)
    twin = _world((iv,))
    diff = changed(flatten(base), flatten(twin))
    assert {v for v, _e in diff if v.startswith("macro.")} <= {"macro.policy_rate", "macro.credit_index"}
    assert ("macro.policy_rate", decl.ENTITY_WORLD) in diff


def test_registry_is_actually_needed_a_wrong_dag_is_caught():
    """Mutation check: if the DAG omitted a real edge, the invariance test must fail.

    Remove the ``rate_shock_bps`` input from ``world.macro_rates`` and confirm the descendant
    set no longer covers what the engine really changed.
    """

    from app.causal.registry import MechanismRegistry

    broken = MechanismRegistry()
    broken.declare_exogenous("rate_shock_bps", "world", intervenable=True)
    for spec in REG.mechanisms.values():
        if spec.id == "world.macro_rates":
            spec = type(spec)(
                id=spec.id,
                version=spec.version,
                scope=spec.scope,
                inputs=tuple(i for i in spec.inputs if i != "rate_shock_bps"),
                outputs=spec.outputs,
                rng_addresses=spec.rng_addresses,
                tier_of_outputs=spec.tier_of_outputs,
            )
        broken.register(spec)
    broken.declare_exogenous("supplier_failure", "company")
    broken.validate()

    base = _world()
    iv, target = _nodes("rate_shock_bps", None, base)
    twin = _world((iv,))
    descendants = broken.descendants([target], sector_of_company=_sector_map(base))
    escaped = changed(flatten(base), flatten(twin)) - descendants
    assert escaped, "a missing DAG edge went undetected"
