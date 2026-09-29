"""M6.1 -- MechanismSpec registry rules: single writer, scopes, lags, cycle rejection."""

from __future__ import annotations

import pytest

from app.causal import (
    CausalCycleError,
    MechanismRegistry,
    MechanismSpec,
    RegistryStructureError,
)


def spec(mid, inputs=(), outputs=(), scope="company", version=1, **kw):
    return MechanismSpec(
        id=mid, version=version, scope=scope, inputs=tuple(inputs), outputs=tuple(outputs), **kw
    )


def registry(*specs, exogenous=()):
    reg = MechanismRegistry()
    for name, scope in exogenous:
        reg.declare_exogenous(name, scope)
    for s in specs:
        reg.register(s)
    return reg


# ------------------------------------------------------------------ cycles


def test_two_mechanism_cycle_is_rejected_with_the_cycle_named():
    reg = registry(spec("a", ["y"], ["x"]), spec("b", ["x"], ["y"]))
    with pytest.raises(CausalCycleError) as info:
        reg.validate()
    assert set(info.value.cycle) == {"a", "b"}
    assert info.value.cycle[0] == info.value.cycle[-1]


def test_three_mechanism_cycle_is_rejected():
    reg = registry(spec("a", ["z"], ["x"]), spec("b", ["x"], ["y"]), spec("c", ["y"], ["z"]))
    with pytest.raises(CausalCycleError) as info:
        reg.validate()
    assert set(info.value.cycle) == {"a", "b", "c"}


def test_same_period_self_read_is_a_cycle():
    reg = registry(spec("a", ["x"], ["x"]))
    with pytest.raises(CausalCycleError):
        reg.validate()


def test_cycle_hidden_behind_an_acyclic_prefix_is_still_found():
    reg = registry(
        spec("root", [], ["r"]),
        spec("a", ["r", "y"], ["x"]),
        spec("b", ["x"], ["y"]),
        spec("leaf", ["y"], ["l"]),
    )
    with pytest.raises(CausalCycleError) as info:
        reg.validate()
    assert set(info.value.cycle) == {"a", "b"}


def test_lagged_self_feedback_is_allowed():
    reg = registry(spec("a", ["x@t-1"], ["x"])).validate()
    assert reg.period_dag() == ("a",)


def test_lagged_cycle_across_mechanisms_is_allowed():
    reg = registry(spec("a", ["y@t-1"], ["x"]), spec("b", ["x"], ["y"])).validate()
    assert reg.period_dag() == ("a", "b")


def test_lag_only_breaks_the_edge_it_marks():
    # b reads x same-period and y lagged; a reads y same-period: a <- b <- a is a cycle via x/y
    reg = registry(spec("a", ["y"], ["x"]), spec("b", ["x", "y@t-1"], ["y"]))
    with pytest.raises(CausalCycleError):
        reg.validate()


# ------------------------------------------------------------------ structure


def test_two_writers_for_one_variable_are_rejected():
    reg = registry(spec("a", [], ["x"]), spec("b", [], ["x"]))
    with pytest.raises(RegistryStructureError, match="more than one writer"):
        reg.validate()


def test_duplicate_mechanism_id_is_rejected():
    reg = registry(spec("a", [], ["x"]))
    with pytest.raises(RegistryStructureError, match="registered twice"):
        reg.register(spec("a", [], ["y"]))


def test_input_without_producer_or_exogenous_declaration_is_rejected():
    reg = registry(spec("a", ["ghost"], ["x"]))
    with pytest.raises(RegistryStructureError, match="no producer"):
        reg.validate()


def test_lagged_input_must_still_reference_a_real_variable():
    reg = registry(spec("a", ["ghost@t-1"], ["x"]))
    with pytest.raises(RegistryStructureError, match="no producer"):
        reg.validate()


def test_exogenous_variable_cannot_also_have_a_producer():
    reg = registry(spec("a", [], ["shock"]), exogenous=[("shock", "world")])
    with pytest.raises(RegistryStructureError):
        reg.validate()


def test_world_mechanism_cannot_read_a_company_variable():
    reg = registry(spec("c", [], ["c_out"]), spec("w", ["c_out"], ["w_out"], scope="world"))
    with pytest.raises(RegistryStructureError, match="narrower-scope"):
        reg.validate()


def test_sector_mechanism_cannot_read_a_company_variable():
    reg = registry(spec("c", [], ["c_out"]), spec("s", ["c_out"], ["s_out"], scope="sector"))
    with pytest.raises(RegistryStructureError, match="narrower-scope"):
        reg.validate()


def test_company_mechanism_may_read_sector_and_world_variables():
    reg = registry(
        spec("w", [], ["w_out"], scope="world"),
        spec("s", ["w_out"], ["s_out"], scope="sector"),
        spec("c", ["s_out", "w_out"], ["c_out"]),
    ).validate()
    assert reg.period_dag() == ("w", "s", "c")


def test_output_cannot_be_lagged_and_a_mechanism_needs_an_output():
    with pytest.raises(RegistryStructureError):
        spec("a", [], ["x@t-1"])
    with pytest.raises(RegistryStructureError):
        spec("a", [], [])
    with pytest.raises(RegistryStructureError):
        spec("a", [], ["x"], version=0)
    with pytest.raises(RegistryStructureError):
        spec("a", ["i", "i"], ["x"])


def test_cannot_mark_an_unknown_variable_intervenable():
    reg = registry(spec("a", [], ["x"])).validate()
    with pytest.raises(RegistryStructureError):
        reg.allow_intervention("nope")


# ------------------------------------------------------------------ determinism


def test_topological_order_is_insertion_order_independent():
    specs = [spec("m3", ["b"], ["c"]), spec("m1", [], ["a"]), spec("m2", ["a"], ["b"]), spec("m0", [], ["z"])]
    forward = registry(*specs).period_dag()
    backward = registry(*reversed(specs)).period_dag()
    assert forward == backward == ("m0", "m1", "m2", "m3")


def test_registry_hash_is_stable_and_sensitive():
    base = registry(spec("a", [], ["x"]), spec("b", ["x"], ["y"]))
    same = registry(spec("b", ["x"], ["y"]), spec("a", [], ["x"]))
    bumped = registry(spec("a", [], ["x"], version=2), spec("b", ["x"], ["y"]))
    rewired = registry(spec("a", [], ["x"]), spec("b", ["x@t-1"], ["y"]))
    assert base.registry_hash() == same.registry_hash()
    assert len({base.registry_hash(), bumped.registry_hash(), rewired.registry_hash()}) == 3


# ------------------------------------------------------------------ descendants

SECTORS = {"COMPANY:A1": "Tech", "COMPANY:A2": "Tech", "COMPANY:B1": "Energy"}


def _demo():
    return registry(
        spec("macro", [], ["gdp"], scope="world"),
        spec("industry", ["gdp"], ["momentum"], scope="sector"),
        spec("demand", ["momentum", "demand@t-1"], ["demand"]),
        spec("revenue", ["demand"], ["revenue"]),
        spec("noise", [], ["noise"]),
        exogenous=[("shock", "world")],
    ).validate()


def _desc(reg, var, entity):
    return reg.descendants([(var, entity)], sector_of_company=SECTORS)


def test_company_intervention_stays_inside_the_company():
    out = _desc(_demo(), "demand", "COMPANY:A1")
    assert out == {("demand", "COMPANY:A1"), ("revenue", "COMPANY:A1")}


def test_sector_variable_reaches_only_companies_in_that_sector():
    out = _desc(_demo(), "momentum", "SECTOR:Tech")
    assert {e for _v, e in out if _v == "revenue"} == {"COMPANY:A1", "COMPANY:A2"}


def test_world_variable_reaches_every_sector_and_company():
    out = _desc(_demo(), "gdp", "WORLD")
    assert {e for v, e in out if v == "momentum"} == {"SECTOR:Tech", "SECTOR:Energy"}
    assert {e for v, e in out if v == "revenue"} == set(SECTORS)
    assert ("noise", "COMPANY:A1") not in out


def test_unrelated_variables_are_never_descendants():
    out = _desc(_demo(), "gdp", "WORLD")
    assert not any(v == "noise" for v, _e in out)


def test_lagged_edges_are_followed():
    reg = registry(spec("a", ["x@t-1"], ["y"]), spec("b", ["y"], ["x"])).validate()
    out = reg.descendants([("x", "COMPANY:A1")], sector_of_company=SECTORS)
    assert ("y", "COMPANY:A1") in out and ("x", "COMPANY:A1") in out


def test_unknown_target_is_rejected():
    with pytest.raises(RegistryStructureError):
        _demo().descendants([("nope", "WORLD")], sector_of_company=SECTORS)


def test_exogenous_target_with_no_consumers_is_only_itself():
    out = _desc(_demo(), "shock", "WORLD")
    assert out == {("shock", "WORLD")}
