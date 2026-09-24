"""Contract and regression tests for SEMANTIC_RNG_V3 adoption in World V2."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from app.economy.v2 import EconomyParamsV2, WorldOutcomeV2, build_economy, run_economy
from app.export_world_v2 import export_economy_v2
from app.world.rng import (
    NAMESPACE_VERSION,
    TRANSFORM_VERSION,
    TRANSFORM_VERSIONS,
    SemanticRNG,
    StreamAddress,
    StreamCollisionError,
    StreamRegistry,
    normal,
    raw_uint64,
    semantic_rng_world_id,
    uniform01,
)

WORLD = "W1"
RAW_A_P3_D0_N4 = [
    17808481158497600400,
    12907484391326523584,
    11772879509484593154,
    1275222375816255461,
]
UNIFORM_A_P3_D0_N4 = [
    0.9653996980355135,
    0.6997161309199478,
    0.6382090770296638,
    0.06912994351310542,
]
NORMAL_N_P0_D0_N3 = [
    -0.028834719748263446,
    -1.1949102813352988,
    0.9563500928831609,
]
UNIFORM_ADDRESS = StreamAddress(WORLD, "ENT-001", "demand", "unit_sales", "uniform01@T1")
NORMAL_ADDRESS = StreamAddress(WORLD, "ENT-001", "demand", "shock", "normal@T1")


def test_semantic_rng_v3_golden_vectors_match_canonical_kernel() -> None:
    assert raw_uint64(UNIFORM_ADDRESS, 3, 0, 4).tolist() == RAW_A_P3_D0_N4
    assert uniform01(UNIFORM_ADDRESS, 3, 0, 4).tolist() == UNIFORM_A_P3_D0_N4
    np.testing.assert_array_max_ulp(
        normal(NORMAL_ADDRESS, 0, 0, 3),
        np.array(NORMAL_N_P0_D0_N3),
        maxulp=4,
    )


def test_semantic_rng_v3_multi_block_and_semantic_coordinates_are_isolated() -> None:
    baseline = raw_uint64(UNIFORM_ADDRESS, 3, 0, 8)
    next_period = raw_uint64(UNIFORM_ADDRESS, 4, 0, 8)
    next_draw = raw_uint64(UNIFORM_ADDRESS, 3, 1, 8)

    assert baseline[:4].tolist() == RAW_A_P3_D0_N4
    assert set(baseline).isdisjoint(next_period)
    assert set(baseline).isdisjoint(next_draw)


def test_semantic_stream_is_coordinate_addressed_and_order_independent() -> None:
    context = SemanticRNG("world-alpha", 17)
    stream = context.stream("COMPANY:AAAA", "operations")
    expected = stream.uniform("return", 3, 2)

    stream.normal("demand", 8, 1)
    context.stream("COMPANY:BBBB", "operations").uniform("return", 3, 2)

    assert stream.uniform("return", 3, 2) == expected
    assert (
        stream.raw("custom", "uniform01@T1", 3, 2)
        == raw_uint64(
            StreamAddress(
                context.rng_world_id,
                "COMPANY:AAAA",
                "operations",
                "custom",
                "uniform01@T1",
            ),
            3,
            2,
        )[0]
    )


def test_public_world_and_seed_bridge_without_a_v3_seed_field() -> None:
    assert semantic_rng_world_id("world-alpha", 17) == '["world-alpha",17]'
    assert "seed" not in StreamAddress.__dataclass_fields__

    first = SemanticRNG("world-alpha", 17).stream("COMPANY:AAAA", "operations")
    second = SemanticRNG("world-alpha", 18).stream("COMPANY:AAAA", "operations")
    other_world = SemanticRNG("world-beta", 17).stream("COMPANY:AAAA", "operations")

    baseline = first.raw("return", "uniform01@T1", 5, 1)
    assert second.raw("return", "uniform01@T1", 5, 1) != baseline
    assert other_world.raw("return", "uniform01@T1", 5, 1) != baseline


def test_stream_registry_is_deterministic_and_rejects_distinct_owners() -> None:
    registry = StreamRegistry()
    registry.claim(UNIFORM_ADDRESS, "demand")
    registry.claim(UNIFORM_ADDRESS, "demand")
    registry.claim(NORMAL_ADDRESS, "risk")

    with pytest.raises(StreamCollisionError, match="already owned"):
        registry.claim(UNIFORM_ADDRESS, "pricing")

    manifest = registry.manifest()
    assert manifest == sorted(manifest, key=lambda entry: entry["address"].encode())
    assert manifest == [
        {
            "address": NORMAL_ADDRESS.canonical().decode(),
            "owner": "risk",
            "transform_version": TRANSFORM_VERSION,
        },
        {
            "address": UNIFORM_ADDRESS.canonical().decode(),
            "owner": "demand",
            "transform_version": TRANSFORM_VERSION,
        },
    ]


def test_tickers_are_unique_across_bounded_seeds() -> None:
    for seed in range(64):
        _, roster = build_economy(
            EconomyParamsV2(years=1, seed=seed),
            world_id=f"ticker-world-{seed}",
            company_count=24,
        )
        tickers = [company["ticker"] for company in roster]
        assert len(tickers) == len(set(tickers)) == 24
        assert all(len(ticker) == 4 and ticker.isascii() and ticker.isalpha() for ticker in tickers)


def test_supplied_roster_is_validated() -> None:
    params = EconomyParamsV2(years=1, seed=1)
    company = {"ticker": "AAAA", "name": "Alpha", "sector": "Technology"}

    with pytest.raises(ValueError, match="must not be empty"):
        run_economy(params, roster=[])
    with pytest.raises(ValueError, match="missing fields"):
        run_economy(params, roster=[{"ticker": "AAAA"}])
    with pytest.raises(ValueError, match="unknown sector"):
        run_economy(params, roster=[{**company, "sector": "Unknown"}])
    with pytest.raises(ValueError, match="duplicated"):
        run_economy(params, roster=[company, company])
    with pytest.raises(ValueError, match="does not match"):
        run_economy(params, roster=[company], company_count=2)


def _entity_trace(world: WorldOutcomeV2, ticker: str) -> dict[str, object]:
    return {
        "initial_shares": world.initial_shares[ticker],
        "quarters": [row for row in world.quarters if row.company == ticker],
        "balance_sheets": [row for row in world.balance_sheets if row.company == ticker],
        "cash_flows": [row for row in world.cash_flows if row.company == ticker],
        "filings": [row for row in world.filings if row.company == ticker],
        "earnings": [row for row in world.earnings if row.company == ticker],
        "estimates": [row for row in world.estimates if row.company == ticker],
        "prices": [row for row in world.prices if row.company == ticker],
        "revisions": [row for row in world.revisions if row.company == ticker],
        "events": [row for row in world.events if row.company == ticker],
        "latents": world.latents[ticker],
        "defaults": [row for row in world.defaults if row["company"] == ticker],
        "fraud_windows": [row for row in world.fraud_windows if row["company"] == ticker],
    }


def _entity_bytes(world: WorldOutcomeV2, ticker: str) -> bytes:
    def encode_special(value: object) -> object:
        if isinstance(value, date):
            return value.isoformat()
        if is_dataclass(value) and not isinstance(value, type):
            return asdict(value)
        raise TypeError(f"cannot encode {type(value).__name__}")

    return json.dumps(
        _entity_trace(world, ticker),
        default=encode_special,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_roster_membership_and_order_do_not_shift_existing_entities() -> None:
    params = EconomyParamsV2(years=2, seed=314159)
    small = run_economy(params, world_id="membership-world", company_count=3)
    large = run_economy(params, world_id="membership-world", company_count=4)
    reordered = run_economy(
        params,
        world_id="membership-world",
        roster=list(reversed(small.companies)),
    )

    assert small.companies == large.companies[:3]
    for company in small.companies:
        ticker = company["ticker"]
        expected = _entity_bytes(small, ticker)
        assert _entity_bytes(large, ticker) == expected
        assert _entity_bytes(reordered, ticker) == expected


def test_initial_shares_use_one_explicit_semantic_coordinate() -> None:
    params = EconomyParamsV2(years=1, seed=2718)
    world = run_economy(params, world_id="shares-world", company_count=1)
    ticker = world.companies[0]["ticker"]
    stream = SemanticRNG(world.world_id, params.seed).stream(f"COMPANY:{ticker}", "initial_state")

    size = math.exp(params.size_dispersion * stream.normal("size", 0))
    base_revenue = 250e6 * size
    expected = max(1.0, round((base_revenue / 25.0) * stream.uniform_range("initial_shares", 0.7, 1.3, 0)))

    assert world.initial_shares == {ticker: expected}


def test_world_outcome_claims_roster_company_sector_and_world_addresses() -> None:
    world = run_economy(
        EconomyParamsV2(years=1, seed=99),
        world_id="registry-world",
        company_count=3,
    )
    addresses = [json.loads(entry["address"]) for entry in world.stream_registry]
    entities = {payload[2] for payload in addresses}

    assert world.rng_namespace == NAMESPACE_VERSION
    assert world.rng_transform_versions == TRANSFORM_VERSIONS
    assert world.rng_world_id == semantic_rng_world_id(world.world_id, world.seed)
    assert world.world_id == "registry-world"
    assert all(len(payload) == 6 and payload[0] == NAMESPACE_VERSION for payload in addresses)
    assert {"WORLD", "ROSTER:000", "ROSTER:001", "ROSTER:002"} <= entities
    assert any(entity.startswith("COMPANY:") for entity in entities)
    assert any(entity.startswith("SECTOR:") for entity in entities)
    assert all(entry["transform_version"] == TRANSFORM_VERSION for entry in world.stream_registry)


def test_hidden_registry_and_manifest_hash_preserve_public_sealing(tmp_path: Path) -> None:
    world = run_economy(
        EconomyParamsV2(years=1, seed=17),
        world_id="export-world",
        company_count=2,
    )
    output = export_economy_v2(world, tmp_path / "release")
    hidden = json.loads((output / "hidden" / "world_state.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())

    assert hidden["rng_namespace"] == NAMESPACE_VERSION
    assert hidden["rng_transform_versions"] == TRANSFORM_VERSIONS
    assert hidden["stream_registry"] == world.stream_registry
    assert manifest["rng_namespace"] == NAMESPACE_VERSION
    assert manifest["rng_transform_versions"] == TRANSFORM_VERSIONS
    assert "stream_registry" not in manifest
    assert "rng_world_id" not in manifest

    canonical_registry = json.dumps(
        world.stream_registry,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert manifest["rng_registry_sha256"] == hashlib.sha256(canonical_registry).hexdigest()

    public_paths = sorted((output / "public").glob("*.json")) + [output / "manifest.json"]
    public_text = "\n".join(path.read_text() for path in public_paths)
    for forbidden in ("stream_registry", "latents", "fraud_windows", "rng_world_id"):
        assert forbidden not in public_text
    for hidden_detail in ("ROSTER:", "COMPANY:", "SECTOR:", world.rng_world_id):
        assert hidden_detail not in public_text
    for entry in world.stream_registry:
        assert entry["address"] not in public_text
        payload = json.loads(entry["address"])
        assert set(entry) == {"address", "owner", "transform_version"}
        assert payload[0] == NAMESPACE_VERSION
        assert payload[1] == world.rng_world_id


def test_distribution_guards_reject_mismatched_transforms() -> None:
    with pytest.raises(ValueError, match="distribution_id"):
        normal(UNIFORM_ADDRESS, 0, 0)
    with pytest.raises(ValueError, match="distribution_id"):
        uniform01(NORMAL_ADDRESS, 0, 0)
