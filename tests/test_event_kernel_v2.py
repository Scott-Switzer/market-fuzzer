import pytest

from app.exchange.v2 import (
    CancelOrderCommandV2,
    DeterministicSchedulerV2,
    EventKernelV2,
    EventKindV2,
    ExchangeValidationError,
    OrderCommandV2,
    OrderEventV2,
    OrderRejectedError,
    OrderTypeV2,
    RunManifestV2,
    SideV2,
    TimeInForceV2,
)


def manifest() -> RunManifestV2:
    return RunManifestV2("spec", "artifact", "generator", "commitment", "seed-digest")


def command(*, command_id: str = "cmd-1", order_id: str = "order-1", time_ns: int = 100) -> OrderCommandV2:
    return OrderCommandV2(
        command_id, order_id, "acct", "NOVA", SideV2.BUY, OrderTypeV2.LIMIT, 10, time_ns, 1, 100
    )


def test_identical_manifest_and_commands_produce_byte_equivalent_replay() -> None:
    first, second = EventKernelV2(manifest()), EventKernelV2(manifest())
    for kernel in (first, second):
        kernel.admit(command())
        kernel.admit(command(command_id="cmd-2", order_id="order-2", time_ns=120))
    replay = first.ledger.replay(manifest(), first.ledger.events)
    assert first.ledger.canonical_bytes() == second.ledger.canonical_bytes() == replay.canonical_bytes()
    assert first.ledger.digest == second.ledger.digest == replay.digest


def test_scheduler_uses_declared_total_order_not_insertion_order() -> None:
    scheduler = DeterministicSchedulerV2()
    late = OrderEventV2("event-z", EventKindV2.COMMAND_ACCEPTED, 10, 2, 3, "c2", "o2")
    first = OrderEventV2("event-a", EventKindV2.COMMAND_ACCEPTED, 10, 2, 3, "c1", "o1")
    earlier = OrderEventV2("event-b", EventKindV2.COMMAND_ACCEPTED, 9, 9, 9, "c3", "o3")
    for event in (late, first, earlier):
        scheduler.schedule(event)
    assert [event.event_id for event in scheduler.drain()] == ["event-b", "event-a", "event-z"]


def test_validation_and_duplicate_commands_fail_with_typed_errors() -> None:
    with pytest.raises(ExchangeValidationError):
        OrderCommandV2("bad", "order", "acct", "NOVA", SideV2.BUY, OrderTypeV2.LIMIT, 0, 0, 0, 1)
    kernel = EventKernelV2(manifest())
    assert kernel.admit(command()).kind == EventKindV2.ORDER_ACKNOWLEDGED
    with pytest.raises(OrderRejectedError, match="duplicate command_id"):
        kernel.admit(command())
    rejected = kernel.admit(command(command_id="cmd-2"))
    assert rejected.kind == EventKindV2.ORDER_REJECTED
    assert rejected.payload == {"reason": "duplicate_order_id"}


def test_time_in_force_rejects_unprotected_fok_market_orders() -> None:
    with pytest.raises(ExchangeValidationError, match="price protection"):
        OrderCommandV2(
            "fok-market",
            "order",
            "acct",
            "NOVA",
            SideV2.BUY,
            OrderTypeV2.MARKET,
            10,
            0,
            0,
            time_in_force=TimeInForceV2.FOK,
        )


def test_cancel_command_has_its_own_idempotent_transport_acknowledgement() -> None:
    kernel = EventKernelV2(manifest())
    cancel = CancelOrderCommandV2("cancel-1", "order-1", "acct", 100, 1)
    acknowledgement = kernel.admit_cancel(cancel)
    assert acknowledgement.kind == EventKindV2.COMMAND_ACCEPTED
    assert acknowledgement.payload == {"command_type": "cancel", "orig_order_id": "order-1"}
    with pytest.raises(OrderRejectedError, match="duplicate command_id"):
        kernel.admit_cancel(cancel)
