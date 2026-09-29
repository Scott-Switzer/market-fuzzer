"""M6.2 -- the World V2 declaration matches the engine that it describes."""

from __future__ import annotations

import ast
import json
from dataclasses import fields
from pathlib import Path

import pytest

from app.causal import world_v2 as decl
from app.economy import v2 as engine
from app.economy.v2 import EconomyEngineV2, EconomyParamsV2

REG = decl.build_world_v2_registry()

# columns that identify a row and are therefore not values produced by a mechanism
KEYS = {
    "quarters": {"company", "sector", "fiscal_year", "fiscal_quarter", "period_end", "fraud_flag"},
    "balance_sheets": {"company", "period_end"},
    "cash_flows": {"company", "period_end"},
    "filings": {"company", "period_end"},
    "earnings": {"company", "period_end"},
    "estimates": {"company", "metric", "period"},
    "revisions": {"company", "metric", "period"},
    "prices": {"company", "session"},
}
CLASSES = {
    "quarters": engine.QuarterRowV2,
    "balance_sheets": engine.BalanceSheetV2,
    "cash_flows": engine.CashFlowV2,
    "filings": engine.FilingEventV2,
    "earnings": engine.EarningsEventV2,
    "estimates": engine.EstimateRowV2,
    "revisions": engine.RevisionV2,
    "prices": engine.PriceRowV2,
}


def _outputs(prefix: str) -> set[str]:
    return {name.split(".", 1)[1] for name in REG.variables() if name.startswith(prefix + ".")}


@pytest.mark.parametrize("table", sorted(CLASSES))
def test_every_output_column_has_exactly_one_registered_writer(table):
    columns = {f.name for f in fields(CLASSES[table])} - KEYS[table]
    registered = _outputs(table)
    if table == "quarters":
        columns |= {"fraud_flag"}
    assert registered == columns, f"{table}: registry and dataclass disagree"


def test_macro_columns_are_covered():
    columns = {f.name for f in fields(engine.MacroStateV2)} - {"date"}
    assert _outputs("macro") == columns


def test_latent_snapshot_keys_match_the_engine_snapshot():
    outcome = EconomyEngineV2(EconomyParamsV2(years=1), company_count=3).run()
    engine_keys = set(next(iter(outcome.latents.values()))[0].values)
    assert engine_keys == set(decl.LATENT_SOURCES)
    assert {f"latents.{k}" for k in engine_keys} <= set(REG.variables())


def test_rng_addresses_match_the_stream_manifest_exactly():
    outcome = EconomyEngineV2(EconomyParamsV2(years=6, seed=7), company_count=10).run()
    seen: set[str] = set()
    for row in outcome.stream_registry:
        _ns, _world, entity, mechanism, variable, dist = json.loads(row["address"])
        seen.add(f"{entity.split(':')[0]}/{mechanism}/{variable}/{dist}")
    declared = [a for spec in REG.mechanisms.values() for a in spec.rng_addresses]
    assert len(declared) == len(set(declared)), "an RNG address is claimed by two mechanisms"
    assert set(declared) == seen


def test_every_engine_clamp_is_a_registered_intervenable_variable():
    tree = ast.parse(Path(engine.__file__).read_text())
    clamped = {
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_clamp"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
    }
    assert clamped == set(decl.CLAMPABLE)
    assert REG.intervenable_variables() == frozenset(decl.CLAMPABLE)


def test_period_order_is_topological_and_hash_is_pinned_across_builds():
    order = REG.period_dag()
    index = {mid: i for i, mid in enumerate(order)}
    for spec in REG.mechanisms.values():
        for ref in spec.inputs:
            name, lagged = ref.removesuffix("@t-1"), ref.endswith("@t-1")
            producer = REG.producer(name)
            if producer is not None and not lagged:
                assert index[producer] < index[spec.id]
    assert decl.build_world_v2_registry().registry_hash() == REG.registry_hash()


def test_company_scope_intervention_does_not_cross_companies():
    """Companies are causally isolated: a demand clamp on X never reaches Y's revenue or prices."""
    out = REG.descendants(
        [("demand_index", "COMPANY:X")], sector_of_company={"COMPANY:X": "Energy", "COMPANY:Y": "Energy"}
    )
    assert ("prices.close", "COMPANY:Y") not in out
    assert ("quarters.revenue", "COMPANY:Y") not in out
