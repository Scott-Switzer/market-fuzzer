import pytest
from pydantic import ValidationError

from app.schemas import WorldSpec
from app.world import build_demo_world


def test_world_round_trip_and_stable_hash():
    spec = build_demo_world(7)
    restored = WorldSpec.model_validate_json(spec.canonical_json())
    assert restored == spec
    assert restored.specification_hash() == spec.specification_hash()
    assert len(spec.specification_hash()) == 64


def test_world_rejects_unknown_target_and_out_of_clock_event():
    data = build_demo_world().model_dump()
    data["experiment"]["target_asset"] = "FAKE"
    with pytest.raises(ValidationError, match="target_asset"):
        WorldSpec.model_validate(data)
    data = build_demo_world().model_dump()
    data["events"][0]["simulation_step"] = 9999
    with pytest.raises(ValidationError, match="outside"):
        WorldSpec.model_validate(data)


def test_world_rejects_non_lot_parent_quantity():
    data = build_demo_world().model_dump()
    data["exchange"]["lot_size"] = 10
    data["experiment"]["parent_order"]["quantity"] = 101
    with pytest.raises(ValidationError, match="lot_size"):
        WorldSpec.model_validate(data)
