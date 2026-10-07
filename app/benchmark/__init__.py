"""Synthetic Exchange Benchmark vertical slice (M10.6).

Joins the M10 synthetic universe, the V2 price-time-priority exchange, deterministic
background agents, and the versioned external-agent observation/action protocol
into one reproducible synthetic trading session with sealed evaluation worlds.
M10.6 adds three process families and the familiar / distribution / mechanism
evaluation partitions behind a market-process generalization gap.
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
from app.benchmark.model import (
    EVALUATION_VALID,
    INVALID_AGENT_PROTOCOL,
    INVALID_AGENT_UNAVAILABLE,
    INVALID_INTERNAL,
    FillRecord,
    SessionResult,
    TaskKind,
    TaskOutcome,
    TaskSpec,
)
from app.benchmark.port import (
    AGENT_PROTOCOL,
    AGENT_UNAVAILABLE,
    HttpJsonPort,
    InProcessPort,
    StrategyDecisionPort,
    accumulate_port,
    crossing_limit_action,
    hold_action,
    passive_maker_port,
    replace_action,
    submit_limit_action,
    twap_port,
)
from app.benchmark.runner import BenchmarkReport, WorldOutcome, builtin_port_factory, run_benchmark
from app.benchmark.session import BenchmarkSession, SessionConfig
from app.benchmark.tasks import build_task_spec, evaluate, max_drawdown_cents
from app.benchmark.universe import (
    DISTRIBUTION_ECOLOGY,
    FAMILIAR_ECOLOGY,
    HIDDEN_PROFILE,
    PUBLIC_PROFILE,
    BenchmarkUniverse,
    EcologyProfile,
    EvaluationPartition,
    Security,
    build_universe,
)
from app.market.process import (
    FAMILIAR_FAMILY,
    MECHANISM_FAMILIES,
    MarkovRegimeJumpFactorT,
    ProcessFamily,
    ProcessFamilyKind,
    StochasticVolFactorT,
)

__all__ = [
    "AGENT_PROTOCOL",
    "AGENT_UNAVAILABLE",
    "EVALUATION_VALID",
    "INVALID_AGENT_PROTOCOL",
    "INVALID_AGENT_UNAVAILABLE",
    "INVALID_INTERNAL",
    "BackgroundAgent",
    "BenchmarkReport",
    "BenchmarkSession",
    "BenchmarkUniverse",
    "DISTRIBUTION_ECOLOGY",
    "BookView",
    "CancelIntent",
    "EcologyProfile",
    "EvaluationPartition",
    "FAMILIAR_ECOLOGY",
    "FAMILIAR_FAMILY",
    "FillRecord",
    "FundamentalTraderAgent",
    "HIDDEN_PROFILE",
    "HttpJsonPort",
    "InProcessPort",
    "MECHANISM_FAMILIES",
    "MarkovRegimeJumpFactorT",
    "MarketMakerAgent",
    "MomentumTraderAgent",
    "NoiseTraderAgent",
    "PUBLIC_PROFILE",
    "ProcessFamily",
    "ProcessFamilyKind",
    "Security",
    "SessionConfig",
    "SessionResult",
    "StochasticVolFactorT",
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
    "replace_action",
    "run_benchmark",
    "submit_limit_action",
    "twap_port",
]
