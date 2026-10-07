"""Synthetic Exchange Benchmark vertical slice (M10.5).

Joins the M10 synthetic universe, the V2 price-time-priority exchange, deterministic
background agents, and the versioned external-agent observation/action protocol
into one reproducible synthetic trading session with sealed evaluation worlds.
"""

from app.benchmark.agents import (
    BackgroundAgent,
    BookView,
    CancelIntent,
    FundamentalTraderAgent,
    MarketMakerAgent,
    MomentumTraderAgent,
    NoiseTraderAgent,
    SubmitIntent,
    build_background_agents,
)
from app.benchmark.hashing import canonical_json, digest, digest_many
from app.benchmark.model import FillRecord, SessionResult, TaskKind, TaskOutcome, TaskSpec
from app.benchmark.port import (
    HttpJsonPort,
    InProcessPort,
    StrategyDecisionPort,
    accumulate_port,
    crossing_limit_action,
    hold_action,
    passive_maker_port,
    submit_limit_action,
    twap_port,
)
from app.benchmark.runner import BenchmarkReport, WorldOutcome, builtin_port_factory, run_benchmark
from app.benchmark.session import BenchmarkSession, SessionConfig
from app.benchmark.tasks import build_task_spec, evaluate, max_drawdown_cents
from app.benchmark.universe import (
    HIDDEN_PROFILE,
    PUBLIC_PROFILE,
    BenchmarkUniverse,
    HoldoutProfile,
    Security,
    build_universe,
)

__all__ = [
    "BackgroundAgent",
    "BenchmarkReport",
    "BenchmarkSession",
    "BenchmarkUniverse",
    "BookView",
    "CancelIntent",
    "FillRecord",
    "FundamentalTraderAgent",
    "HIDDEN_PROFILE",
    "HoldoutProfile",
    "HttpJsonPort",
    "InProcessPort",
    "MarketMakerAgent",
    "MomentumTraderAgent",
    "NoiseTraderAgent",
    "PUBLIC_PROFILE",
    "Security",
    "SessionConfig",
    "SessionResult",
    "StrategyDecisionPort",
    "SubmitIntent",
    "TaskKind",
    "TaskOutcome",
    "TaskSpec",
    "WorldOutcome",
    "accumulate_port",
    "build_background_agents",
    "build_task_spec",
    "build_universe",
    "builtin_port_factory",
    "canonical_json",
    "crossing_limit_action",
    "digest",
    "digest_many",
    "evaluate",
    "hold_action",
    "max_drawdown_cents",
    "passive_maker_port",
    "run_benchmark",
    "submit_limit_action",
    "twap_port",
]
