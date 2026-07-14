from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .models import WorldSpec


@dataclass(order=True)
class Order:
    """One resting order; sequence implements price-time priority within a price level."""

    price: float
    sequence: int
    side: str = field(compare=False)
    quantity: int = field(compare=False)
    owner: str = field(compare=False)


class LimitOrderBook:
    """Minimal price-time-priority central limit order book for one synthetic issuer."""

    def __init__(self, symbol: str, tick: float = 0.01) -> None:
        self.symbol, self.tick, self._sequence = symbol, tick, 0
        self.bids: list[Order] = []
        self.asks: list[Order] = []
        self.last_price: float | None = None
        self.trade_log: list[dict] = []

    def add_limit(self, side: str, price: float, quantity: int, owner: str) -> None:
        if quantity <= 0:
            return
        price = round(round(price / self.tick) * self.tick, 2)
        self._sequence += 1
        order = Order(price, self._sequence, side, quantity, owner)
        (self.bids if side == "buy" else self.asks).append(order)
        self._sort()

    def cancel_owner(self, owner: str) -> None:
        self.bids = [order for order in self.bids if order.owner != owner]
        self.asks = [order for order in self.asks if order.owner != owner]

    def _sort(self) -> None:
        self.bids.sort(key=lambda order: (-order.price, order.sequence))
        self.asks.sort(key=lambda order: (order.price, order.sequence))

    def market(self, side: str, quantity: int, owner: str) -> list[dict]:
        """Consume best price first; partial fills are explicit and residual is unfilled."""
        fills: list[dict] = []
        opposite = self.asks if side == "buy" else self.bids
        remaining = quantity
        while remaining and opposite:
            resting = opposite[0]
            amount = min(remaining, resting.quantity)
            fills.append({"price": resting.price, "quantity": amount, "maker": resting.owner, "taker": owner})
            self.trade_log.append({"symbol": self.symbol, "price": resting.price, "quantity": amount, "buy_owner": owner if side == "buy" else resting.owner, "sell_owner": resting.owner if side == "buy" else owner})
            self.last_price = resting.price
            remaining -= amount
            resting.quantity -= amount
            if resting.quantity == 0:
                opposite.pop(0)
        return fills

    def snapshot(self) -> dict:
        best_bid = self.bids[0].price if self.bids else None
        best_ask = self.asks[0].price if self.asks else None
        mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else self.last_price
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
            "bid_depth": sum(order.quantity for order in self.bids[:8]),
            "ask_depth": sum(order.quantity for order in self.asks[:8]),
            "bids": [{"price": order.price, "quantity": order.quantity} for order in self.bids[:5]],
            "asks": [{"price": order.price, "quantity": order.quantity} for order in self.asks[:5]],
        }


@dataclass
class RunResult:
    spec: dict
    timeline: list[dict]
    summary: dict
    world: dict

    def to_dict(self) -> dict:
        return {"spec": self.spec, "timeline": self.timeline, "summary": self.summary, "world": self.world}


def _company_world(spec: WorldSpec, rng: random.Random) -> dict[str, dict]:
    sectors = ("Software", "Industrial", "Biotech")
    beta = (1.25, 0.8, 1.1)
    return {
        symbol: {
            "name": f"{symbol} Synthetic {sectors[index]}",
            "sector": sectors[index],
            "beta": beta[index],
            "fundamental": spec.initial_price * (0.88 + rng.random() * 0.24),
            "history": [],
        }
        for index, symbol in enumerate(spec.symbols)
    }


def _refresh_market_maker(book: LimitOrderBook, fair_value: float, base_depth: int, spread_bps: float, depth_multiplier: float) -> None:
    book.cancel_owner("market_maker")
    for level in range(1, 4):
        half_spread = fair_value * spread_bps / 20_000 * level
        quantity = max(10, int(base_depth * depth_multiplier / level))
        book.add_limit("buy", fair_value - half_spread, quantity, "market_maker")
        book.add_limit("sell", fair_value + half_spread, quantity, "market_maker")


def _scenario_effect(spec: WorldSpec, step: int) -> tuple[float, float, str | None]:
    depth_multiplier, macro_shock, event = 1.0, 0.0, None
    if spec.scenario == "liquidity_withdrawal" and step >= spec.event_step:
        depth_multiplier, event = 0.35, "Liquidity providers withdraw from the synthetic exchange"
    elif spec.scenario == "earnings_shock" and step == spec.event_step:
        macro_shock, event = -0.07, "NOVA releases a synthetic negative earnings surprise"
    elif spec.scenario == "crowded_unwind" and step >= spec.event_step:
        depth_multiplier, macro_shock, event = 0.52, -0.012, "Crowded momentum funds unwind"
    return depth_multiplier, macro_shock, event


def run_world(spec: WorldSpec) -> RunResult:
    """Run a seeded synthetic micro-market with companies, macro state, agents, events and a CLOB.

    The strategy is a buy-side TWAP parent order. Its market orders consume real resting
    liquidity, so fill quality responds to the synthetic agents and scenario conditions.
    """
    rng = random.Random(spec.seed)
    companies = _company_world(spec, rng)
    books = {symbol: LimitOrderBook(symbol) for symbol in spec.symbols}
    for symbol, company in companies.items():
        _refresh_market_maker(books[symbol], company["fundamental"], spec.base_depth, spec.base_spread_bps, 1.0)

    benchmark = companies[spec.symbols[0]]["fundamental"]
    remaining, executed, notional = spec.parent_order_shares, 0, 0.0
    macro_level = 0.0
    timeline: list[dict] = []
    events: list[dict] = []

    for step in range(spec.steps):
        depth_multiplier, event_shock, event = _scenario_effect(spec, step)
        macro_level += rng.gauss(0, spec.volatility * 0.55) + (event_shock * 0.12 if step == spec.event_step else 0)
        if event:
            events.append({"step": step, "message": event})

        # World state moves before agents act; every issuer has distinct macro beta and noise.
        for symbol, company in companies.items():
            shock = event_shock if symbol == spec.symbols[0] and step == spec.event_step else 0.0
            company["fundamental"] *= 1 + company["beta"] * rng.gauss(0, spec.volatility * 0.35) + macro_level * 0.05 + shock
            _refresh_market_maker(books[symbol], company["fundamental"], spec.base_depth, spec.base_spread_bps, depth_multiplier)

        for symbol, book in books.items():
            snap = book.snapshot()
            mid = snap["mid"] or companies[symbol]["fundamental"]
            deviation = (companies[symbol]["fundamental"] / mid) - 1 if mid else 0.0
            # Fundamental trader acts on private value; momentum uses the synthetic trade path; noise provides uninformed flow.
            if abs(deviation) > 0.0015:
                book.market("buy" if deviation > 0 else "sell", rng.randint(8, 28), "fundamental_trader")
            history = companies[symbol]["history"]
            if len(history) >= 3 and history[-1] > history[-3]:
                book.market("buy", rng.randint(4, 18), "momentum_trader")
            elif len(history) >= 3:
                book.market("sell", rng.randint(4, 18), "mean_reversion_trader")
            book.market("buy" if rng.random() > 0.5 else "sell", rng.randint(2, 12), "noise_trader")

        if spec.scenario == "crowded_unwind" and step >= spec.event_step:
            books[spec.symbols[0]].market("sell", int(spec.base_depth * 0.3), "forced_liquidator")

        desired = min(remaining, max(1, math.ceil(spec.parent_order_shares / spec.steps)))
        fills = books[spec.symbols[0]].market("buy", desired, "experimental_twap")
        filled = sum(fill["quantity"] for fill in fills)
        executed += filled
        remaining -= filled
        notional += sum(fill["price"] * fill["quantity"] for fill in fills)

        principal = books[spec.symbols[0]].snapshot()
        for symbol, book in books.items():
            snapshot = book.snapshot()
            companies[symbol]["history"].append(snapshot["mid"] or companies[symbol]["fundamental"])
        bid, ask = principal["best_bid"], principal["best_ask"]
        spread_bps = ((ask - bid) / ((ask + bid) / 2) * 10_000) if bid and ask else None
        timeline.append({
            "step": step,
            "macro_level": round(macro_level, 5),
            "mid": round(principal["mid"] or benchmark, 4),
            "fundamental": round(companies[spec.symbols[0]]["fundamental"], 4),
            "spread_bps": round(spread_bps, 2) if spread_bps else None,
            "bid_depth": principal["bid_depth"],
            "ask_depth": principal["ask_depth"],
            "fill": filled,
            "remaining": remaining,
            "event": event,
            "book": {"bids": principal["bids"], "asks": principal["asks"]},
        })

    avg_price = notional / executed if executed else 0.0
    shortfall = ((avg_price / benchmark) - 1) * 10_000 if executed else 0.0
    final = timeline[-1]
    return RunResult(
        spec=spec.to_dict(), timeline=timeline,
        summary={
            "fill_rate": round(executed / spec.parent_order_shares, 4), "executed_shares": executed,
            "unfilled_shares": remaining, "average_execution_price": round(avg_price, 4),
            "implementation_shortfall_bps": round(shortfall, 2), "final_mid": final["mid"],
            "final_spread_bps": final["spread_bps"], "reproduction": f"seed={spec.seed}; scenario={spec.scenario}; strategy=experimental_twap",
            "limitations": "Seeded agent-based prototype; not calibrated to historical order-book data or validated for deployment.",
        },
        world={"macro": {"name": "Synthetic risk regime", "final_state": round(macro_level, 5)}, "companies": companies, "events": events,
               "agent_ecology": ["market_maker", "fundamental_trader", "momentum_trader", "mean_reversion_trader", "noise_trader", "forced_liquidator", "experimental_twap"]},
    )


def run_scenario_battery(base: WorldSpec) -> dict:
    runs = []
    for offset, scenario in enumerate(("normal", "liquidity_withdrawal", "earnings_shock", "crowded_unwind")):
        spec = WorldSpec(**{**base.__dict__, "scenario": scenario, "seed": base.seed + offset})
        runs.append(run_world(spec).to_dict())
    worst = max(runs, key=lambda run: run["summary"]["implementation_shortfall_bps"])
    return {"runs": runs, "failure_surface": {
        "worst_scenario": worst["spec"]["scenario"], "worst_shortfall_bps": worst["summary"]["implementation_shortfall_bps"],
        "finding": "The full world holds company fundamentals, macro state, market-making depth, heterogeneous agents, events, and a price-time-priority book. This result identifies the worst observed execution-cost world for the current strategy size.",
    }}
