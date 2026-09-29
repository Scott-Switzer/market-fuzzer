"""M7 producer semantics found by independent QC.

1. Public events carry only values implied by the filed statements, never the latent margin.
2. operating_expenses is the filed total (SG&A + depreciation).
3. The hidden tier carries the causal graph, and the manifest carries reproducibility data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from app.causal.export import GRAPH_SCHEMA, reachable_variables
from app.causal.world_v2 import build_world_v2_registry
from app.economy.v2 import EconomyParamsV2, InterventionV2, run_economy
from app.export_world_v2 import export_economy_v2


@pytest.fixture(scope="module")
def fraud_world():
    ivs = (InterventionV2("EQNS", "fraud_propensity", 0.99, date(2026, 6, 30)),)
    return run_economy(EconomyParamsV2(years=2, seed=20260921), ivs, "fuzzer-000000")


@pytest.fixture(scope="module")
def released(tmp_path_factory, fraud_world) -> Path:
    out = tmp_path_factory.mktemp("rel")
    export_economy_v2(fraud_world, out)
    return out


def test_fraud_window_exists_and_inflates_the_published_margin(fraud_world):
    assert any(w["company"] == "EQNS" for w in fraud_world.fraud_windows)
    inflated = [q for q in fraud_world.quarters if q.company == "EQNS" and q.fraud_flag]
    assert inflated


def test_earnings_call_margin_equals_published_margin_even_under_fraud(fraud_world):
    published = {(q.company, q.period_end): q.gross_margin for q in fraud_world.quarters}
    calls = [e for e in fraud_world.events if e.kind == "earnings_call"]
    assert calls
    checked_fraud = 0
    for event in calls:
        (period_end,) = [pe for (c, pe) in published if c == event.company and (pe - event.at).days == -30]
        margin = published[(event.company, period_end)]
        assert abs(event.payload["gross_margin"] - margin) <= 5e-7
        checked_fraud += any(
            q.fraud_flag
            for q in fraud_world.quarters
            if q.company == event.company and q.period_end == period_end
        )
    assert checked_fraud >= 1, "the fraud quarters must actually be compared"


def test_operating_expenses_is_sga_plus_depreciation(fraud_world):
    cash = {(r.company, r.period_end): r.depreciation for r in fraud_world.cash_flows}
    for q in fraud_world.quarters:
        assert q.operating_expenses > cash[(q.company, q.period_end)]
        assert q.gross_profit - q.operating_expenses == pytest.approx(q.ebit, rel=1e-9, abs=1e-6)


def test_hidden_causal_graph_is_exported_and_hashed(released):
    graph = json.loads((released / "hidden" / "causal_graph.json").read_text())
    manifest = json.loads((released / "manifest.json").read_text())
    registry = build_world_v2_registry().validate()
    assert graph["schema"] == GRAPH_SCHEMA
    assert graph["registry_hash"] == registry.registry_hash() == manifest["causal_registry_hash"]
    assert graph["registry"] == registry.to_canonical()
    assert len(graph["interventions"]) == len(graph["intervention_reach"]) == 1
    assert graph["intervention_reach"][0] == reachable_variables(registry, ["fraud_propensity"])
    assert "hidden/causal_graph.json" in manifest["hidden_artifacts"]
    assert "hidden/causal_graph.json" not in manifest["public_artifacts"]


def test_events_do_not_read_latent_truth_in_the_declared_graph():
    canonical = build_world_v2_registry().validate().to_canonical()
    for mech in canonical["mechanisms"]:
        if any(out.startswith("events.") for out in mech["outputs"]):
            assert "margin.gross_true" not in mech["inputs"], mech["id"]
    earnings = next(m for m in canonical["mechanisms"] if m["id"] == "company.earnings_call_event")
    assert "quarters.gross_margin" in earnings["inputs"]


def test_manifest_records_environment_and_logical_hashes(released):
    manifest = json.loads((released / "manifest.json").read_text())
    env = manifest["environment"]
    assert set(env) == {"python_version", "numpy_version", "platform", "lock_file", "lock_sha256"}
    assert len(env["lock_sha256"]) == 64
    logical = {}
    for rel, entry in manifest["artifact_hashes"].items():
        doc = json.loads((released / rel).read_text())
        canon = json.dumps(doc, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        assert entry["logical_sha256"] == hashlib.sha256(canon).hexdigest()
        logical[rel] = entry["logical_sha256"]
    canon = json.dumps(logical, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    assert manifest["world_logical_sha256"] == hashlib.sha256(canon).hexdigest()


def test_same_seed_gives_identical_logical_hash(tmp_path):
    hashes = []
    for name in ("a", "b"):
        out = tmp_path / name
        export_economy_v2(run_economy(EconomyParamsV2(years=1, seed=7), (), "fuzzer-000000"), out)
        hashes.append(json.loads((out / "manifest.json").read_text())["world_logical_sha256"])
    assert hashes[0] == hashes[1]
