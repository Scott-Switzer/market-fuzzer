import pytest

from app.exchange import Account, CancelRequest, Exchange, Order, OrderType, Side
from app.schemas import ExchangeSpec


def make_exchange() -> Exchange:
    exchange = Exchange(["NOVA"], ExchangeSpec(lot_size=10, maker_fee_bps=0, taker_fee_bps=1))
    for agent in ("seller1", "seller2", "buyer", "intruder"):
        exchange.register(Account(agent, 10_000_000, {"NOVA": 0}))
    return exchange


def limit(order_id: str, agent: str, side: Side, price: int, qty: int = 100) -> Order:
    return Order(order_id, agent, "NOVA", side, OrderType.LIMIT, qty, 0, price)


def test_crossed_limit_matches_at_resting_price_and_leaves_partial_remainder():
    exchange = make_exchange()
    exchange.submit(limit("s1", "seller1", Side.SELL, 101, 100), 0)
    trades = exchange.submit(limit("b1", "buyer", Side.BUY, 103, 40), 1)
    assert [(trade.price_ticks, trade.quantity) for trade in trades] == [(101, 40)]
    assert exchange.books["NOVA"].orders["s1"].remaining == 60
    assert exchange.books["NOVA"].best_bid is None


def test_market_order_walks_prices_and_fifo_within_price():
    exchange = make_exchange()
    exchange.submit(limit("s1", "seller1", Side.SELL, 101, 50), 0)
    exchange.submit(limit("s2", "seller2", Side.SELL, 101, 50), 0)
    exchange.submit(limit("s3", "seller2", Side.SELL, 102, 50), 0)
    order = Order("m1", "buyer", "NOVA", Side.BUY, OrderType.MARKET, 120, 1)
    trades = exchange.submit(order, 1)
    assert [trade.maker_order_id for trade in trades] == ["s1", "s2", "s3"]
    assert [trade.quantity for trade in trades] == [50, 50, 20]
    assert exchange.books["NOVA"].orders["s3"].remaining == 30


def test_owner_safe_cancel_duplicate_and_lot_checks():
    exchange = make_exchange()
    exchange.submit(limit("s1", "seller1", Side.SELL, 101), 0)
    with pytest.raises(PermissionError):
        exchange.cancel(CancelRequest("s1", "intruder", 1), "NOVA")
    exchange.cancel(CancelRequest("s1", "seller1", 1), "NOVA")
    with pytest.raises(ValueError, match="duplicate"):
        exchange.submit(limit("s1", "seller1", Side.SELL, 101), 2)
    with pytest.raises(ValueError, match="lot size"):
        exchange.submit(limit("bad-lot", "seller1", Side.SELL, 101, 11), 2)


def test_cash_inventory_and_fees_reconcile():
    exchange = make_exchange()
    initial_cash = exchange.total_cash_cents()
    exchange.submit(limit("s1", "seller1", Side.SELL, 101, 100), 0)
    exchange.submit(Order("m1", "buyer", "NOVA", Side.BUY, OrderType.MARKET, 100, 1), 1)
    assert exchange.total_cash_cents() == initial_cash
    assert exchange.total_inventory("NOVA") == 0
    assert exchange.accounts["buyer"].inventory["NOVA"] == 100
    assert exchange.accounts["seller1"].inventory["NOVA"] == -100


def test_halt_rejects_orders_but_allows_cancel():
    exchange = make_exchange()
    exchange.submit(limit("s1", "seller1", Side.SELL, 101), 0)
    exchange.books["NOVA"].halt(1, 3)
    with pytest.raises(RuntimeError, match="halted"):
        exchange.submit(limit("b1", "buyer", Side.BUY, 100), 2)
    exchange.cancel(CancelRequest("s1", "seller1", 2), "NOVA")
    exchange.submit(limit("b2", "buyer", Side.BUY, 100), 4)
