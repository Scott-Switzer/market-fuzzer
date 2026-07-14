from hypothesis import given, settings
from hypothesis import strategies as st

from app.exchange import Account, Exchange, Order, OrderType, Side
from app.schemas import ExchangeSpec


@given(
    st.lists(
        st.tuples(
            st.sampled_from([Side.BUY, Side.SELL]),
            st.integers(min_value=95, max_value=105),
            st.integers(min_value=1, max_value=20).map(lambda value: value * 10),
        ),
        min_size=1,
        max_size=80,
    )
)
@settings(max_examples=35, deadline=None)
def test_random_limit_sequences_preserve_book_invariants(instructions):
    exchange = Exchange(["NOVA"], ExchangeSpec(lot_size=10, maker_fee_bps=0, taker_fee_bps=0))
    exchange.register(Account("agent", 1_000_000_000, {"NOVA": 0}))
    for index, (side, price, quantity) in enumerate(instructions):
        exchange.submit(
            Order(f"O{index}", "agent", "NOVA", side, OrderType.LIMIT, quantity, index, price), index
        )
        exchange.books["NOVA"].assert_valid()
        assert exchange.total_inventory("NOVA") == 0
        assert all((order.remaining or 0) > 0 for order in exchange.books["NOVA"].orders.values())


@given(st.integers(min_value=1, max_value=1000))
def test_order_rejects_non_positive_quantities(quantity):
    order = Order("valid", "agent", "NOVA", Side.BUY, OrderType.MARKET, quantity, 0)
    assert order.remaining == quantity
