"""Shared data contracts for the M10.5 synthetic exchange benchmark.

These types are deliberately free of engine dependencies so that the session,
task scorers, and runner can depend on them without import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "EVALUATION_VALID",
    "INVALID_AGENT_PROTOCOL",
    "INVALID_AGENT_UNAVAILABLE",
    "INVALID_INTERNAL",
    "FillRecord",
    "SessionResult",
    "TaskKind",
    "TaskOutcome",
    "TaskSpec",
]

# Evaluation validity states. A benchmark is only scoreable when it is VALID.
EVALUATION_VALID = "VALID"
INVALID_AGENT_UNAVAILABLE = "INVALID_AGENT_UNAVAILABLE"
INVALID_AGENT_PROTOCOL = "INVALID_AGENT_PROTOCOL"
INVALID_INTERNAL = "INVALID_INTERNAL"


class TaskKind(StrEnum):
    """The three M10.5 benchmark tasks."""

    EXECUTION = "execution"
    MARKET_MAKING = "market_making"
    PORTFOLIO = "portfolio"


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """The concrete instruction handed to one external agent in one world."""

    kind: TaskKind
    focus_symbol: str
    side: str
    target_quantity: int
    agent_instruments: tuple[str, ...]
    initial_position_per_instrument: int
    max_order_quantity: int

    def __post_init__(self) -> None:
        if self.side not in {"buy", "sell"}:
            raise ValueError("task side must be 'buy' or 'sell'")
        if self.target_quantity < 0 or self.initial_position_per_instrument < 0:
            raise ValueError("task quantities must be non-negative")
        if self.max_order_quantity < 1:
            raise ValueError("max_order_quantity must be positive")
        if not self.agent_instruments:
            raise ValueError("a task needs at least one agent instrument")
        if self.focus_symbol not in self.agent_instruments:
            raise ValueError("the focus symbol must be one of the agent instruments")


@dataclass(frozen=True, slots=True)
class FillRecord:
    """One execution of the external agent's own order.

    ``notional_cents`` is the executed consideration in account currency, so
    downstream metrics never have to guess at the tick size.
    """

    instrument_id: str
    side: str
    quantity: int
    price_ticks: int
    step_index: int
    day_index: int
    is_maker: bool
    notional_cents: int


@dataclass(frozen=True, slots=True)
class SessionResult:
    """Everything the task scorers need from one completed synthetic session."""

    world_id: str
    universe_id: str
    holdout: str
    sessions: tuple[str, ...]
    ledger_digest: str
    market_logical_sha256: str
    action_digest: str
    event_count: int
    order_count: int
    cancel_count: int
    replace_count: int
    trade_count: int
    background_trade_count: int
    expired_day_order_count: int
    instruments: tuple[str, ...]
    focus_symbol: str
    steps_total: int
    tick_size_cents: int
    arrival_price_ticks: int
    final_price_ticks: int
    agent_fills: tuple[FillRecord, ...]
    agent_maker_fill_count: int
    agent_taker_fill_count: int
    agent_filled_quantity: int
    agent_net_delivered_quantity: int
    agent_cash_cents: int
    agent_positions: dict[str, int]
    agent_peak_inventory: dict[str, int]
    agent_initial_value_cents: int
    agent_final_value_cents: int
    equity_curve_cents: tuple[int, ...]
    quote_uptime: float
    violations: tuple[str, ...]
    agent_failure: str | None
    agent_failure_count: int
    scoreable: bool


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """A scored task outcome for one world."""

    kind: str
    score: float
    metrics: dict[str, float | int]
    violations: tuple[str, ...]
