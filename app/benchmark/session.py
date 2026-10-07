"""One complete synthetic trading session (M10.5).

This is the join point of the vertical slice: the M10 synthetic universe, the V2
price-time-priority matching exchange, deterministic background agents, and the
versioned external-agent observation/action protocol all meet here.

The session is fully deterministic. Every exchange command is stamped with a
globally monotonic (time, sequence) pair from a single internal clock, so the V2
event ledger is totally ordered regardless of which agent acted first.

Sealed-evaluation rules enforced here:

* Model-facing identifiers never reveal the world index, the evaluation
  partition, the process family, the ecology, the seed, or generator parameters.
  The agent session identifier is an opaque digest over evaluator-private seed
  material, and the process family is *evaluator-private*: it is recorded in the
  run manifest and the replay package but never reaches an observation.
* Order ownership is tracked explicitly; maker/taker attribution never relies on
  parsing an order-ID string.
* The external agent only ever holds and trades the task's tradable instruments.
* A transport or protocol failure of the agent is recorded as an evaluation
  validity failure, not silently absorbed as a legitimate decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.benchmark.agents import (
    BackgroundAgent,
    BookView,
    CancelIntent,
    SubmitIntent,
    build_background_agents,
)
from app.benchmark.hashing import digest, digest_many
from app.benchmark.model import FillRecord, SessionResult, TaskKind, TaskSpec
from app.benchmark.port import StrategyDecisionPort
from app.benchmark.universe import BenchmarkUniverse, EcologyProfile, Security
from app.exchange.v2 import (
    CancelOrderCommandV2,
    EventKernelV2,
    ExchangeValidationError,
    OrderCommandV2,
    OrderRejectedError,
    OrderTypeV2,
    ReplaceOrderCommandV2,
    RunManifestV2,
    SideV2,
    TimeInForceV2,
)
from app.exchange.v2_matching import AccountRiskLimitsV2, AccountStateV2, MatchingExchangeV2, TradeV2
from app.strategy_protocol import StrategyActionV2, StrategyObservationV2, StrategyOpenOrderV2
from app.world.rng import SemanticRNG, SemanticStream

__all__ = ["BenchmarkSession", "SessionConfig", "run_manifest"]

AGENT_ACCOUNT = "agent"
AGENT_SESSION_NAMESPACE = "fwf-benchmark-agent-session-v1"
_REJECTED_KINDS = frozenset({"order_rejected", "cancel_rejected", "replace_rejected"})


def run_manifest(
    *,
    universe: BenchmarkUniverse,
    ecology: EcologyProfile,
    task: TaskSpec,
    port_name: str,
) -> RunManifestV2:
    """The evaluator-side commitment record for one world.

    Depends only on the world's public specification, the task, and the agent
    artifact, so the runner that owns the campaign can reproduce exactly the
    manifest a session committed to rather than re-deriving the digests (and
    risking a drift between the two). The process family reaches the ledger only
    as a digest; it never reaches an observation.
    """

    return RunManifestV2(
        specification_digest=digest(
            {
                "universe": universe.universe_id,
                "task": task.kind.value,
                "partition": universe.partition,
                "ecology": ecology.label,
                "process_family": universe.process_family,
                "target_quantity": task.target_quantity,
            }
        ),
        strategy_artifact_digest=digest({"port": port_name}),
        generator_bundle_digest=digest(
            {
                "generator": "benchmark-session-v2",
                "process_family": universe.process_family,
                "market": universe.market_logical_sha256,
            }
        ),
        campaign_commitment=digest({"world": universe.world_id, "seed": universe.seed}),
        seed_material_digest=digest({"seed": universe.seed, "process_family": universe.process_family}),
    )


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Venue and agent-resource parameters for one session."""

    steps_per_day: int = 30
    recent_window: int = 8
    background_cash_cents: int = 10**15
    background_position: int = 5_000_000
    agent_cash_cents: int = 10**13
    tick_size_cents: int = 1
    market_order_collar_bps: int = 500

    def __post_init__(self) -> None:
        if self.steps_per_day < 1:
            raise ValueError("steps_per_day must be positive")
        if self.recent_window < 1:
            raise ValueError("recent_window must be positive")
        if self.tick_size_cents < 1:
            raise ValueError("tick_size_cents must be positive")
        if self.market_order_collar_bps > 10_000:
            raise ValueError("market order collar cannot exceed 100 percent")


class BenchmarkSession:
    """Run one synthetic universe through the V2 exchange with an external agent."""

    def __init__(
        self,
        *,
        universe: BenchmarkUniverse,
        ecology: EcologyProfile,
        task: TaskSpec,
        port: StrategyDecisionPort,
        config: SessionConfig | None = None,
    ) -> None:
        self.universe = universe
        self.ecology = ecology
        self.task = task
        self.port = port
        self.config = config or SessionConfig()

    def run(self) -> SessionResult:
        cfg = self.config
        securities = tuple(self.universe.securities)
        instruments = tuple(security.symbol for security in securities)
        rng = SemanticRNG(self.universe.world_id, self.universe.seed)
        agents = build_background_agents(self.ecology)
        # Instrument-specific streams: a given agent must not make an identical
        # random decision in every security at the same step.
        agent_streams: dict[tuple[str, str], SemanticStream] = {
            (agent.agent_id, instrument): rng.stream(
                f"AGENT:{agent.agent_id}:{instrument}", "benchmark.agent"
            )
            for agent in agents
            for instrument in instruments
        }

        manifest = run_manifest(
            universe=self.universe,
            ecology=self.ecology,
            task=self.task,
            port_name=self.port.name,
        )
        exchange = MatchingExchangeV2(
            EventKernelV2(manifest),
            tick_size_cents=cfg.tick_size_cents,
            maker_fee_bps=self.ecology.maker_fee_bps,
            taker_fee_bps=self.ecology.taker_fee_bps,
        )
        for agent in agents:
            exchange.register(
                AccountStateV2(
                    agent.account_id,
                    cfg.background_cash_cents,
                    {instrument: cfg.background_position for instrument in instruments},
                )
            )
        # Starting inventory exists only for instruments the task allows the agent
        # to trade, so an inaccessible security can never move the agent's P&L.
        agent_start_positions = {
            symbol: self.task.initial_position_per_instrument
            for symbol in self.task.agent_instruments
            if self.task.initial_position_per_instrument > 0
        }
        exchange.register(
            AccountStateV2(AGENT_ACCOUNT, cfg.agent_cash_cents, agent_start_positions),
            risk_limits=AccountRiskLimitsV2(max_order_quantity=self.task.max_order_quantity),
        )

        self._exchange = exchange
        self._instruments = instruments
        self._clock = 0
        self._order_sequence = 0
        self._order_owner: dict[str, str] = {}
        self._mark: dict[str, int] = {}
        self._recent: dict[str, list[int]] = {instrument: [] for instrument in instruments}
        self._fundamental: dict[str, int] = {}
        self._fills: list[FillRecord] = []
        self._equity: list[int] = []
        # The step ordinal a fill belongs to. Kept separate from len(self._equity)
        # because the equity curve carries a pre-decision baseline entry, so its
        # length is not the trading step.
        self._current_step = 0
        self._action_digests: list[str] = []
        self._observed_order_ids: set[str] = set()
        self._violations: set[str] = set()
        self._order_count = 0
        self._cancel_count = 0
        self._replace_count = 0
        self._trade_count = 0
        self._background_trade_count = 0
        self._expired_day_order_count = 0
        self._focus_buy_quantity = 0
        self._focus_sell_quantity = 0
        self._peak_inventory: dict[str, int] = {
            symbol: abs(quantity) for symbol, quantity in agent_start_positions.items()
        }
        self._quote_steps = 0
        self._eligible_steps = 0
        self._arrival_price_ticks: int | None = None
        self._initial_value_cents: int | None = None
        global_step = 0

        for day_index in range(len(self.universe.sessions)):
            if day_index > 0:
                exchange.open_session(exchange_time_ns=self._tick(), venue_sequence=self._tick())
            for step in range(cfg.steps_per_day):
                self._current_step = global_step
                self._advance_marks(day_index, step, securities)
                self._run_background_agents(agents, agent_streams, global_step, day_index)
                if global_step == 0:
                    self._arrival_price_ticks = self._mid_price(self.task.focus_symbol)
                    self._initial_value_cents = self._agent_value()
                    # The drawdown baseline is the pre-decision value: a loss in
                    # the very first decision must be visible in the curve.
                    self._equity.append(self._initial_value_cents)
                self._run_external_agent(global_step, day_index)
                self._equity.append(self._agent_value())
                global_step += 1
            self._expired_day_order_count += self._close_day(exchange)

        account = exchange.accounts[AGENT_ACCOUNT]
        arrival = self._arrival_price_ticks or self._mid_price(self.task.focus_symbol)
        initial_value = self._initial_value_cents or 0
        final_value = self._agent_value()
        failure = getattr(self.port, "first_failure", None)
        failure_count = int(getattr(self.port, "errors", 0) or 0)
        peak_inventory = {
            instrument: max(
                self._peak_inventory.get(instrument, 0),
                abs(account.positions.get(instrument, 0)),
            )
            for instrument in instruments
        }
        return SessionResult(
            world_id=self.universe.world_id,
            universe_id=self.universe.universe_id,
            partition=self.universe.partition,
            process_family=self.universe.process_family,
            sessions=tuple(session.isoformat() for session in self.universe.sessions),
            ledger_digest=exchange.kernel.ledger.digest,
            market_logical_sha256=self.universe.market_logical_sha256,
            action_digest=digest_many(self._action_digests),
            event_count=len(exchange.kernel.ledger.events),
            order_count=self._order_count,
            cancel_count=self._cancel_count,
            replace_count=self._replace_count,
            trade_count=self._trade_count,
            background_trade_count=self._background_trade_count,
            expired_day_order_count=self._expired_day_order_count,
            instruments=self._instruments,
            focus_symbol=self.task.focus_symbol,
            steps_total=global_step,
            tick_size_cents=cfg.tick_size_cents,
            arrival_price_ticks=arrival,
            final_price_ticks=self._price(self.task.focus_symbol),
            agent_fills=tuple(self._fills),
            agent_maker_fill_count=sum(1 for fill in self._fills if fill.is_maker),
            agent_taker_fill_count=sum(1 for fill in self._fills if not fill.is_maker),
            agent_filled_quantity=sum(fill.quantity for fill in self._fills),
            agent_net_delivered_quantity=self._net_delivered(),
            agent_cash_cents=account.cash_cents,
            agent_positions={instrument: account.positions.get(instrument, 0) for instrument in instruments},
            agent_peak_inventory=peak_inventory,
            agent_initial_value_cents=initial_value,
            agent_final_value_cents=final_value,
            equity_curve_cents=tuple(self._equity),
            quote_uptime=self._quote_steps / self._eligible_steps if self._eligible_steps else 0.0,
            violations=tuple(sorted(self._violations)),
            agent_failure=failure,
            agent_failure_count=failure_count,
            scoreable=failure is None,
        )

    def _close_day(self, exchange: MatchingExchangeV2) -> int:
        """Close the trading day and report how many DAY orders expired."""

        before = len(exchange.kernel.ledger.events)
        exchange.close_session(exchange_time_ns=self._tick(), venue_sequence=self._tick())
        return sum(
            1 for event in exchange.kernel.ledger.events[before:] if event.kind.value == "order_cancelled"
        )

    # -- identity and clock helpers ----------------------------------------------

    def session_identifier(self) -> str:
        """An opaque, deterministic, non-informative agent-facing session ID.

        Derived from evaluator-private seed material only, so it is stable for
        replay yet reveals nothing about the world index, the evaluation
        partition, the process family, or the generator parameters. It does not
        depend on the process family at all, so two worlds that differ only by
        their generator family share an identity.
        """

        return (
            "sx-"
            + digest(
                {
                    "namespace": AGENT_SESSION_NAMESPACE,
                    "seed": self.universe.seed,
                    "task": self.task.kind.value,
                }
            )[:32]
        )

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def _next_order_id(self, account_id: str) -> str:
        self._order_sequence += 1
        order_id = f"{account_id}-O{self._order_sequence:09d}"
        self._order_owner[order_id] = account_id
        return order_id

    def _next_command_id(self, prefix: str) -> str:
        self._order_sequence += 1
        return f"{prefix}-{self._order_sequence:09d}"

    def _owns(self, order_id: str) -> bool:
        """Explicit ownership lookup; never inferred from an ID prefix."""

        return self._order_owner.get(order_id) == AGENT_ACCOUNT

    def _net_delivered(self) -> int:
        """Sign-aware parent-order quantity delivered on the focus instrument."""

        if self.task.side == "buy":
            return self._focus_buy_quantity - self._focus_sell_quantity
        return self._focus_sell_quantity - self._focus_buy_quantity

    def _price(self, instrument: str) -> int:
        if instrument in self._mark:
            return self._mark[instrument]
        return self._fundamental.get(instrument) or 1

    def _mid_price(self, instrument: str) -> int:
        bid, ask = self._exchange.best_quote(instrument)
        if bid is not None and ask is not None:
            return max(1, round((bid + ask) / 2))
        return max(1, self._price(instrument))

    def _advance_marks(self, day_index: int, step: int, securities: tuple[Security, ...]) -> None:
        span = max(1, self.config.steps_per_day)
        for security in securities:
            open_ticks = security.daily_open_ticks[day_index]
            close_ticks = security.daily_close_ticks[day_index]
            weight = step / span
            self._fundamental[security.symbol] = max(
                1, round(open_ticks + (close_ticks - open_ticks) * weight)
            )
            if security.symbol not in self._mark:
                self._mark[security.symbol] = open_ticks

    # -- background agents -------------------------------------------------------

    def _run_background_agents(
        self,
        agents: tuple[BackgroundAgent, ...],
        streams: dict[tuple[str, str], SemanticStream],
        step: int,
        day_index: int,
    ) -> None:
        for instrument in self._instruments:
            self._record_recent(instrument)
        for agent in agents:
            for instrument in self._instruments:
                open_ids = tuple(
                    order.order_id for order in self._exchange.open_orders_for(agent.account_id, instrument)
                )
                bid, ask = self._exchange.best_quote(instrument)
                view = BookView(
                    instrument_id=instrument,
                    step=step,
                    fundamental_ticks=self._fundamental[instrument],
                    mid_ticks=self._bid_ask_mid(instrument),
                    best_bid_ticks=bid,
                    best_ask_ticks=ask,
                    last_price_ticks=self._mark.get(instrument),
                    recent_prices=tuple(self._recent[instrument]),
                    open_order_ids=open_ids,
                    depth_scale=self.ecology.depth_scale,
                )
                for intent in agent.act(view, streams[(agent.agent_id, instrument)]):
                    self._apply_background_intent(agent.account_id, instrument, intent, day_index)

    def _record_recent(self, instrument: str) -> None:
        mid = self._bid_ask_mid(instrument)
        if mid is not None:
            self._mark.setdefault(instrument, mid)
            self._recent[instrument].append(mid)
            window = self.config.recent_window
            if len(self._recent[instrument]) > window:
                del self._recent[instrument][:-window]

    def _bid_ask_mid(self, instrument: str) -> int | None:
        bid, ask = self._exchange.best_quote(instrument)
        if bid is None or ask is None:
            return None
        return max(1, round((bid + ask) / 2))

    def _apply_background_intent(
        self, account_id: str, instrument: str, intent: object, day_index: int
    ) -> None:
        if isinstance(intent, CancelIntent):
            cancel_command = CancelOrderCommandV2(
                command_id=self._next_command_id("bg-cancel"),
                order_id=intent.order_id,
                account_id=account_id,
                exchange_time_ns=self._tick(),
                venue_sequence=self._tick(),
            )
            self._safe_cancel(cancel_command)
            return
        if not isinstance(intent, SubmitIntent):
            return
        order_id = self._next_order_id(account_id)
        submit_command = OrderCommandV2(
            command_id=self._next_command_id("bg"),
            order_id=order_id,
            account_id=account_id,
            instrument_id=instrument,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            exchange_time_ns=self._tick(),
            venue_sequence=self._tick(),
            price_ticks=intent.price_ticks,
            time_in_force=intent.time_in_force,
        )
        try:
            trades = self._exchange.submit(submit_command)
        except (OrderRejectedError, ExchangeValidationError):
            return
        self._handle_trades(trades, day_index)

    def _safe_cancel(self, command: CancelOrderCommandV2) -> None:
        try:
            self._exchange.cancel_command(command)
        except (OrderRejectedError, ExchangeValidationError):
            return

    # -- external agent ----------------------------------------------------------

    def _run_external_agent(self, step: int, day_index: int) -> None:
        for instrument in self.task.agent_instruments:
            observation = self._observation(instrument, step)
            self._eligible_steps += 1
            try:
                action = StrategyActionV2.model_validate(self.port.decide(observation))
            except (ValueError, TypeError):
                self._violations.add("invalid_action_document")
                continue
            self._action_digests.append(digest(action.model_dump(mode="json")))
            self._apply_agent_action(action, instrument, day_index)
            if self._exchange.open_orders_for(AGENT_ACCOUNT, instrument):
                self._quote_steps += 1

    def _observation(self, instrument: str, step: int) -> dict[str, object]:
        bid, ask = self._exchange.best_quote(instrument)
        mid = self._mid_price(instrument)
        spread_bps = 0.0 if bid is None or ask is None else (ask - bid) * 10_000.0 / mid
        open_orders = tuple(
            StrategyOpenOrderV2(
                order_id=order.order_id,
                side=order.side.value,
                remaining_quantity=order.remaining_quantity,
                limit_price_ticks=order.limit_price_ticks,
            )
            for order in self._exchange.open_orders_for(AGENT_ACCOUNT, instrument)
        )
        self._observed_order_ids.update(order.order_id for order in open_orders)
        remaining = 0
        if self.task.kind is TaskKind.EXECUTION and instrument == self.task.focus_symbol:
            remaining = max(0, self.task.target_quantity - self._net_delivered())
        account = self._exchange.accounts[AGENT_ACCOUNT]
        return StrategyObservationV2(
            session_id=self.session_identifier(),
            step=step,
            symbol=instrument,
            side=self.task.side,  # type: ignore[arg-type]
            mid_ticks=mid,
            best_bid_ticks=bid,
            best_ask_ticks=ask,
            spread_bps=spread_bps,
            observed_volume=0,
            inventory=account.positions.get(instrument, 0),
            remaining_quantity=remaining,
            exchange_latency_profile="normal",
            intervention_active=False,
            open_orders=open_orders,
        ).model_dump(mode="json")

    def _apply_agent_action(self, action: StrategyActionV2, instrument: str, day_index: int) -> None:
        if action.action_type == "hold":
            return
        if action.action_type == "submit":
            self._apply_agent_submit(action, instrument, day_index)
        elif action.action_type == "cancel":
            self._apply_agent_cancel(action)
        else:
            self._apply_agent_replace(action, day_index)

    def _apply_agent_submit(self, action: StrategyActionV2, instrument: str, day_index: int) -> None:
        assert action.side is not None  # guaranteed by the submit validator
        side = SideV2(action.side)
        price_ticks: int | None
        time_in_force: TimeInForceV2
        if action.order_type == "limit":
            price_ticks = action.limit_price_ticks
            time_in_force = TimeInForceV2.DAY
        else:
            reference = (
                self._exchange.best_quote(instrument)[1]
                if side == SideV2.BUY
                else self._exchange.best_quote(instrument)[0]
            )
            if reference is None:
                reference = self._price(instrument)
            collar = self.config.market_order_collar_bps
            multiplier = 10_000 + collar if side == SideV2.BUY else 10_000 - collar
            rounding = math.ceil if side == SideV2.BUY else math.floor
            price_ticks = max(1, rounding(reference * multiplier / 10_000))
            time_in_force = TimeInForceV2.IOC
        self._order_count += 1
        order_id = self._next_order_id(AGENT_ACCOUNT)
        command = OrderCommandV2(
            command_id=self._next_command_id("agent"),
            order_id=order_id,
            account_id=AGENT_ACCOUNT,
            instrument_id=instrument,
            side=side,
            order_type=OrderTypeV2.LIMIT,
            quantity=action.quantity,
            exchange_time_ns=self._tick(),
            venue_sequence=self._tick(),
            price_ticks=price_ticks,
            time_in_force=time_in_force,
        )
        before = len(self._exchange.kernel.ledger.events)
        try:
            trades = self._exchange.submit(command)
        except (OrderRejectedError, ExchangeValidationError):
            self._violations.add("agent_order_rejected:submit_validation")
            return
        self._handle_trades(trades, day_index)
        self._collect_rejections(before, command.command_id)

    def _apply_agent_cancel(self, action: StrategyActionV2) -> None:
        if action.order_id not in self._observed_order_ids:
            self._violations.add("agent_lifecycle_unknown_order_id")
            return
        self._cancel_count += 1
        command = CancelOrderCommandV2(
            command_id=self._next_command_id("agent-cancel"),
            order_id=action.order_id or "",
            account_id=AGENT_ACCOUNT,
            exchange_time_ns=self._tick(),
            venue_sequence=self._tick(),
        )
        before = len(self._exchange.kernel.ledger.events)
        self._safe_cancel(command)
        self._collect_rejections(before, command.command_id)

    def _apply_agent_replace(self, action: StrategyActionV2, day_index: int) -> None:
        if action.order_id not in self._observed_order_ids:
            self._violations.add("agent_lifecycle_unknown_order_id")
            return
        self._replace_count += 1
        command = ReplaceOrderCommandV2(
            command_id=self._next_command_id("agent-replace"),
            order_id=action.order_id or "",
            account_id=AGENT_ACCOUNT,
            exchange_time_ns=self._tick(),
            venue_sequence=self._tick(),
            quantity=action.quantity,
            price_ticks=action.limit_price_ticks or 0,
        )
        before = len(self._exchange.kernel.ledger.events)
        try:
            _, trades = self._exchange.replace_command(command)
        except (OrderRejectedError, ExchangeValidationError):
            self._violations.add("agent_order_rejected:replace_validation")
            return
        self._handle_trades(trades, day_index)
        self._collect_rejections(before, command.command_id)

    def _collect_rejections(self, before: int, command_id: str) -> None:
        for event in self._exchange.kernel.ledger.events[before:]:
            if event.kind.value in _REJECTED_KINDS and event.command_id == command_id:
                reason = str(event.payload.get("reason", "rejected"))
                self._violations.add(f"agent_order_rejected:{reason}")

    # -- trade bookkeeping -------------------------------------------------------

    def _handle_trades(self, trades: tuple[TradeV2, ...], day_index: int) -> None:
        for trade in trades:
            self._trade_count += 1
            self._mark[trade.instrument_id] = trade.price_ticks
            self._recent[trade.instrument_id].append(trade.price_ticks)
            window = self.config.recent_window
            if len(self._recent[trade.instrument_id]) > window:
                del self._recent[trade.instrument_id][:-window]
            if AGENT_ACCOUNT not in (trade.buyer_account_id, trade.seller_account_id):
                self._background_trade_count += 1
                continue
            side = "buy" if trade.buyer_account_id == AGENT_ACCOUNT else "sell"
            is_maker = self._owns(trade.maker_order_id)
            self._fills.append(
                FillRecord(
                    instrument_id=trade.instrument_id,
                    side=side,
                    quantity=trade.quantity,
                    price_ticks=trade.price_ticks,
                    step_index=self._current_step,
                    day_index=day_index,
                    is_maker=is_maker,
                    notional_cents=(trade.quantity * trade.price_ticks * self.config.tick_size_cents),
                )
            )
            if trade.instrument_id == self.task.focus_symbol:
                if side == "buy":
                    self._focus_buy_quantity += trade.quantity
                else:
                    self._focus_sell_quantity += trade.quantity
            self._update_peak_inventory(trade.instrument_id)

    def _update_peak_inventory(self, instrument: str) -> None:
        position = abs(self._exchange.accounts[AGENT_ACCOUNT].positions.get(instrument, 0))
        if position > self._peak_inventory.get(instrument, 0):
            self._peak_inventory[instrument] = position

    def _agent_value(self) -> int:
        account = self._exchange.accounts[AGENT_ACCOUNT]
        notional = sum(
            position * self._price(instrument) for instrument, position in account.positions.items()
        )
        return account.cash_cents + notional * self.config.tick_size_cents
