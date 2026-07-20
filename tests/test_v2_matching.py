from dataclasses import replace

from app.exchange.v2 import (
    CancelOrderCommandV2,
    EventKernelV2,
    EventKindV2,
    OrderCommandV2,
    OrderTypeV2,
    ReplaceOrderCommandV2,
    RunManifestV2,
    SelfTradePreventionV2,
    SideV2,
    TimeInForceV2,
)
from app.exchange.v2_matching import AccountRiskLimitsV2, AccountStateV2, MatchingExchangeV2, SessionStateV2


def make_exchange(*, buyer_limits: AccountRiskLimitsV2 | None = None) -> MatchingExchangeV2:
    kernel = EventKernelV2(RunManifestV2("spec", "artifact", "generator", "commitment", "seed"))
    exchange = MatchingExchangeV2(kernel, tick_size_cents=1, maker_fee_bps=0, taker_fee_bps=10)
    for account_id, cash, position in (
        ("seller-a", 100_000, 100),
        ("seller-b", 100_000, 100),
        ("buyer", 100_000, 0),
    ):
        exchange.register(
            AccountStateV2(account_id, cash, {"NOVA": position}),
            risk_limits=buyer_limits if account_id == "buyer" else None,
        )
    return exchange


def command(
    order_id: str,
    account_id: str,
    side: SideV2,
    price: int | None,
    *,
    quantity: int = 100,
    sequence: int = 0,
    tif: TimeInForceV2 = TimeInForceV2.DAY,
) -> OrderCommandV2:
    return OrderCommandV2(
        f"command-{order_id}",
        order_id,
        account_id,
        "NOVA",
        side,
        OrderTypeV2.LIMIT if price else OrderTypeV2.MARKET,
        quantity,
        sequence * 100,
        sequence,
        price,
        tif,
    )


def test_price_time_priority_reservation_and_cash_like_settlement() -> None:
    exchange = make_exchange()
    initial_cash = sum(account.cash_cents for account in exchange.accounts.values())
    exchange.submit(command("sell-a", "seller-a", SideV2.SELL, 101, sequence=1))
    exchange.submit(command("sell-b", "seller-b", SideV2.SELL, 101, sequence=2))
    assert exchange.accounts["seller-b"].reserved_positions["NOVA"] == 100
    trades = exchange.submit(command("buy", "buyer", SideV2.BUY, 102, quantity=150, sequence=3))
    assert [trade.maker_order_id for trade in trades] == ["sell-a", "sell-b"]
    assert [trade.quantity for trade in trades] == [100, 50]
    assert exchange.accounts["buyer"].positions["NOVA"] == 150
    assert exchange.accounts["seller-b"].reserved_positions["NOVA"] == 50
    assert (
        sum(account.cash_cents for account in exchange.accounts.values()) + exchange.fee_account_cents
        == initial_cash
    )
    assert sum(account.positions["NOVA"] for account in exchange.accounts.values()) == 200


def test_ioc_does_not_rest_and_fok_does_not_partially_execute() -> None:
    exchange = make_exchange()
    exchange.submit(command("sell-a", "seller-a", SideV2.SELL, 101, sequence=1))
    ioc = exchange.submit(
        command("ioc", "buyer", SideV2.BUY, 101, quantity=150, sequence=2, tif=TimeInForceV2.IOC)
    )
    assert [trade.quantity for trade in ioc] == [100]
    assert "ioc" not in exchange._orders
    exchange = make_exchange()
    exchange.submit(command("sell-a", "seller-a", SideV2.SELL, 101, sequence=1))
    assert (
        exchange.submit(
            command("fok", "buyer", SideV2.BUY, 101, quantity=150, sequence=2, tif=TimeInForceV2.FOK)
        )
        == ()
    )
    assert exchange.accounts["buyer"].reserved_cash_cents == 0
    assert exchange.accounts["seller-a"].positions["NOVA"] == 100


def test_self_trade_prevention_cancels_maker_without_changing_inventory() -> None:
    exchange = make_exchange()
    exchange.register(AccountStateV2("both", 100_000, {"NOVA": 100}))
    exchange.submit(command("own-sell", "both", SideV2.SELL, 101, sequence=1))
    own_buy = command("own-buy", "both", SideV2.BUY, 101, sequence=2)
    own_buy = replace(own_buy, self_trade_prevention=SelfTradePreventionV2.CANCEL_MAKER)
    assert exchange.submit(own_buy) == ()
    assert "own-sell" not in exchange._orders
    assert exchange.accounts["both"].positions["NOVA"] == 100
    assert exchange.accounts["both"].reserved_positions.get("NOVA", 0) == 0


def _latest_rejection_reason(exchange: MatchingExchangeV2) -> str:
    return str(exchange.kernel.ledger.events[-1].payload["reason"])


def test_session_and_instrument_halts_reject_new_orders_but_preserve_cancel_rights() -> None:
    exchange = make_exchange()
    exchange.submit(command("expired", "seller-a", SideV2.SELL, 101, sequence=1))
    exchange.close_session(exchange_time_ns=150, venue_sequence=2)
    assert exchange.session_state == SessionStateV2.CLOSED
    assert "expired" not in exchange._orders
    assert exchange.accounts["seller-a"].reserved_positions.get("NOVA", 0) == 0
    assert exchange.submit(command("closed", "seller-a", SideV2.SELL, 101, sequence=3)) == ()
    assert _latest_rejection_reason(exchange) == "session_closed"

    exchange.open_session(exchange_time_ns=350, venue_sequence=4)
    exchange.submit(command("resting", "seller-a", SideV2.SELL, 101, sequence=5))
    exchange.halt_instrument("NOVA", exchange_time_ns=550, venue_sequence=6)
    assert exchange.submit(command("halted", "buyer", SideV2.BUY, 101, sequence=7)) == ()
    assert _latest_rejection_reason(exchange) == "instrument_halted"
    exchange.cancel(account_id="seller-a", order_id="resting", exchange_time_ns=750, venue_sequence=8)
    assert "resting" not in exchange._orders
    assert exchange.accounts["seller-a"].reserved_positions.get("NOVA", 0) == 0

    exchange.resume_instrument("NOVA", exchange_time_ns=850, venue_sequence=9)
    exchange.submit(command("active", "seller-a", SideV2.SELL, 101, sequence=10))
    assert "active" in exchange._orders


def test_typed_cancel_commands_audit_success_and_rejection_against_the_original_order() -> None:
    exchange = make_exchange()
    exchange.submit(command("resting", "seller-a", SideV2.SELL, 101, sequence=1))
    accepted = CancelOrderCommandV2("cancel-1", "resting", "seller-a", 150, 2)
    assert exchange.cancel_command(accepted) is True
    assert "resting" not in exchange._orders
    assert exchange.kernel.ledger.events[-2].kind == EventKindV2.COMMAND_ACCEPTED
    assert exchange.kernel.ledger.events[-1].kind == EventKindV2.ORDER_CANCELLED
    assert exchange.kernel.ledger.events[-1].command_id == "cancel-1"

    rejected = CancelOrderCommandV2("cancel-2", "resting", "seller-a", 250, 3)
    assert exchange.cancel_command(rejected) is False
    event = exchange.kernel.ledger.events[-1]
    assert event.kind == EventKindV2.CANCEL_REJECTED
    assert event.command_id == "cancel-2"
    assert event.payload == {"orig_order_id": "resting", "reason": "unknown_resting_order"}


def test_risk_limits_reject_quantity_and_notional_before_reservation() -> None:
    exchange = make_exchange(
        buyer_limits=AccountRiskLimitsV2(max_order_quantity=50, max_order_notional_cents=5_000)
    )
    assert exchange.submit(command("too-many", "buyer", SideV2.BUY, 101, quantity=51, sequence=1)) == ()
    assert _latest_rejection_reason(exchange) == "risk_max_order_quantity"
    assert exchange.accounts["buyer"].reserved_cash_cents == 0
    assert exchange.submit(command("too-large", "buyer", SideV2.BUY, 101, quantity=50, sequence=2)) == ()
    assert _latest_rejection_reason(exchange) == "risk_max_order_notional"
    assert exchange.accounts["buyer"].reserved_cash_cents == 0
    exchange.submit(command("bounded", "buyer", SideV2.BUY, 100, quantity=50, sequence=3))
    assert exchange.accounts["buyer"].reserved_cash_cents == 5_005


def test_kill_switch_cancels_resting_orders_releases_reservations_and_fails_closed() -> None:
    exchange = make_exchange()
    exchange.submit(command("resting", "seller-a", SideV2.SELL, 101, sequence=1))
    exchange.set_kill_switch("seller-a", enabled=True, exchange_time_ns=150, venue_sequence=2)
    assert "resting" not in exchange._orders
    assert exchange.accounts["seller-a"].reserved_positions.get("NOVA", 0) == 0
    assert exchange.submit(command("blocked", "seller-a", SideV2.SELL, 101, sequence=3)) == ()
    assert _latest_rejection_reason(exchange) == "kill_switch_enabled"
    exchange.set_kill_switch("seller-a", enabled=False, exchange_time_ns=350, venue_sequence=4)
    exchange.submit(command("re-enabled", "seller-a", SideV2.SELL, 101, sequence=5))
    assert "re-enabled" in exchange._orders
    assert [
        event.kind.value for event in exchange.kernel.ledger.events if event.kind.value.startswith("kill_")
    ] == [
        "kill_switch_enabled",
        "kill_switch_disabled",
    ]


def replace_command(
    order_id: str, account_id: str, quantity: int, price: int, *, sequence: int
) -> ReplaceOrderCommandV2:
    return ReplaceOrderCommandV2(
        f"replace-{order_id}-{sequence}", order_id, account_id, sequence * 100, sequence, quantity, price
    )


def test_replace_same_price_reduction_keeps_priority_but_increase_loses_priority() -> None:
    exchange = make_exchange()
    exchange.submit(command("sell-a", "seller-a", SideV2.SELL, 101, sequence=1))
    exchange.submit(command("sell-b", "seller-b", SideV2.SELL, 101, sequence=2))
    assert exchange.replace(replace_command("sell-b", "seller-b", 50, 101, sequence=3)) == ()
    trades = exchange.submit(command("buy", "buyer", SideV2.BUY, 101, quantity=150, sequence=4))
    assert [(trade.maker_order_id, trade.quantity) for trade in trades] == [("sell-a", 100), ("sell-b", 50)]

    exchange = make_exchange()
    exchange.submit(command("sell-a", "seller-a", SideV2.SELL, 101, quantity=50, sequence=1))
    exchange.submit(command("sell-b", "seller-b", SideV2.SELL, 101, quantity=100, sequence=2))
    assert exchange.replace(replace_command("sell-a", "seller-a", 100, 101, sequence=3)) == ()
    trades = exchange.submit(command("buy", "buyer", SideV2.BUY, 101, quantity=200, sequence=4))
    assert [trade.maker_order_id for trade in trades] == ["sell-b", "sell-a"]
