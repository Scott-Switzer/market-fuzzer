import pytest

from app.generators import (
    CorrelatedLatentFactorGeneratorV1,
    HeterogeneousAgentGeneratorV1,
    RegimeSwitchingPointProcessGeneratorV1,
)


@pytest.mark.parametrize(
    "generator",
    [
        HeterogeneousAgentGeneratorV1(),
        RegimeSwitchingPointProcessGeneratorV1(),
        CorrelatedLatentFactorGeneratorV1(),
    ],
)
def test_generator_is_deterministic_and_discloses_contract(generator) -> None:
    instruments = ("NOVA", "ORBT", "VYNE")
    first, replay = (
        generator.generate(seed=7, instruments=instruments, steps=24),
        generator.generate(seed=7, instruments=instruments, steps=24),
    )
    assert first.digest == replay.digest
    assert first.events == replay.events
    assert first.assumptions and first.limitations and first.supported_claims
    assert first.stylized_fact_diagnostics["event_count"] == len(first.events)
    assert list(first.events) == sorted(first.events, key=lambda event: event.exchange_time_ns)


def test_generator_families_are_distinct_and_do_not_copy_a_common_path() -> None:
    instruments = ("NOVA", "ORBT", "VYNE")
    worlds = [
        generator.generate(seed=11, instruments=instruments, steps=30)
        for generator in (
            HeterogeneousAgentGeneratorV1(),
            RegimeSwitchingPointProcessGeneratorV1(),
            CorrelatedLatentFactorGeneratorV1(),
        )
    ]
    assert len({world.family_id for world in worlds}) == 3
    assert len({world.digest for world in worlds}) == 3
    assert {event.kind for event in worlds[0].events} != {event.kind for event in worlds[1].events}
