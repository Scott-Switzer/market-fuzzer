import pytest

from app.calibration import generated_world_similarity
from app.generators.v1 import CorrelatedLatentFactorGeneratorV1, RegimeSwitchingPointProcessGeneratorV1


def test_similarity_report_detects_exact_generated_return_window_without_retaining_prices() -> None:
    world = RegimeSwitchingPointProcessGeneratorV1().generate(seed=17, instruments=("NOVA",), steps=64)
    prices = [float(event.price_ticks) for event in world.events]
    report = generated_world_similarity(world, [99_999.0, *prices, 100_001.0])

    assert report.exact_return_window_duplicate is True
    assert report.similarity_warning is True
    assert report.nearest_window_correlation == pytest.approx(1.0)
    assert "prices" not in report.model_dump()


def test_similarity_report_rejects_insufficient_or_invalid_reference_paths() -> None:
    world = RegimeSwitchingPointProcessGeneratorV1().generate(seed=17, instruments=("NOVA",), steps=64)
    with pytest.raises(ValueError, match="at least as many"):
        generated_world_similarity(world, [100.0, 101.0, 102.0])


def test_multi_asset_similarity_requires_explicit_instrument_selection() -> None:
    world = CorrelatedLatentFactorGeneratorV1().generate(seed=4, instruments=("NOVA", "ORBIT"), steps=12)
    with pytest.raises(ValueError, match="requires an instrument_id"):
        generated_world_similarity(world, [100.0 + step for step in range(20)])
    report = generated_world_similarity(
        world,
        [100.0 + step for step in range(20)],
        instrument_id="NOVA",
    )
    assert report.generated_return_count > 1
