from dataclasses import replace

from app.exchange.v2 import (
    EventKernelV2,
    OrderCommandV2,
    OrderTypeV2,
    RunManifestV2,
    SelfTradePreventionV2,
    SideV2,
    TimeInForceV2,
)
from app.exchange.v2_matching import AccountStateV2, MatchingExchangeV2


def make_exchange() -> MatchingExchangeV2:
    kernel = EventKernelV2(RunManifestV2("spec", "artifact", "generator", "commitment", "seed"))
    exchange = MatchingExchangeV2(kernel, tick_size_cents=1, maker_fee_bps=0, taker_fee_bps=10)
    for account_id, cash, position in (
        ("seller-a", 100_000, 100),
        ("seller-b", 100_000, 100),
        ("buyer", 100_000, 0),
    ):
        exchange.register(AccountStateV2(account_id, cash, {"NOVA": position}))
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
