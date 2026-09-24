"""MARKET_FUZZER_WORLD_V2 — deterministic fundamental economy engine.

Causal chain implemented:

    latent economic state
        -> macro economy (quarterly)
        -> industry dynamics (per sector, per quarter)
        -> company operations
        -> accounting engine (balance sheet closed every quarter)
        -> management decisions (guidance, dividend, refinancing)
        -> filings / earnings events
        -> analyst estimates (issued BEFORE the print) and revisions
        -> prices (quarterly v0, earnings-surprise reactions)

The world exists numerically first. Language is rendered later.
Everything is seeded, deterministic, and point-in-time stamped, and
intervention-ready: the same seed with different interventions produces
worlds identical everywhere except downstream of the intervened node.

Balance-sheet identity holds exactly every quarter from the pinned accounting kernel:
    assets == liabilities + equity
Cash is maintained by explicit operating, investing, financing, and liquidity
transactions; there is no synthetic balancing entry or independently
manufactured equity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from math import pi as _pi
from math import sin as _sin

from app.economy.accounting_v1 import CompanyAccountingV1
from app.world.rng import NAMESPACE_VERSION, TRANSFORM_VERSIONS, SemanticRNG, SemanticStream

WORLD = "WORLD"
COMPANY_ENTITY_PREFIX = "COMPANY:"
SECTOR_ENTITY_PREFIX = "SECTOR:"

# --------------------------------------------------------------------------- #
# Parameters and interventions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InterventionV2:
    """A named causal intervention applied to one company from a start date.

    Semantics: the intervened variable is *clamped* (do-operator style) from
    `start` until the end of the simulation (or `end` if set). World-level
    variables use company "WORLD".
    """

    company: str
    variable: str
    value: float
    start: date
    end: date | None = None


@dataclass(frozen=True)
class EconomyParamsV2:
    years: int = 8
    seed: int = 20260921
    start_year: int = 2026

    # macro
    gdp_trend: float = 0.025
    gdp_cycle_amplitude: float = 0.02
    gdp_cycle_period_years: float = 6.0
    inflation_trend: float = 0.025
    inflation_amplitude: float = 0.015
    inflation_period_years: float = 4.0
    rates_start: float = 0.03
    regime_states: tuple[str, ...] = ("contraction", "slow", "trend", "expansion")
    regime_persistence_years: float = 2.0

    # industry
    industry_growth_dispersion: float = 0.05
    shock_intensity: float = 1.0

    # company heterogeneity
    margin_dispersion: float = 0.08
    growth_dispersion: float = 0.10
    size_dispersion: float = 0.8
    leverage_dispersion: float = 0.12

    # accounting
    days_receivable: float = 52.0
    days_inventory: float = 70.0
    days_payable: float = 45.0
    capex_rate: float = 0.055
    depreciation_rate: float = 0.05
    tax_rate: float = 0.21
    payout_ratio: float = 0.30
    min_cash_buffer: float = 0.02

    # distress
    distress_threshold: float = 0.15

    # estimates
    analyst_bias: float = 0.012
    analyst_noise: float = 0.05

    # market
    earnings_reaction: float = 0.35
    price_noise: float = 0.02

    def validate(self) -> EconomyParamsV2:
        if not 1 <= self.years <= 30:
            raise ValueError("years must be in [1, 30]")
        if not 0.0 <= self.tax_rate <= 1.0:
            raise ValueError("tax_rate must be in [0, 1]")
        if not 1900 <= self.start_year <= 2200:
            raise ValueError("start_year out of range")
        return self


# --------------------------------------------------------------------------- #
# Latent state and output rows
# --------------------------------------------------------------------------- #


@dataclass
class MacroStateV2:
    date: date
    gdp_growth: float
    inflation: float
    policy_rate: float
    regime: str
    credit_index: float


@dataclass
class LatentAtV2:
    date: date
    values: dict[str, float]


@dataclass
class QuarterRowV2:
    """Public accounting output for one company-quarter (income statement)."""

    company: str
    sector: str
    fiscal_year: int
    fiscal_quarter: int
    period_end: date
    available_at: date

    revenue: float
    cogs: float
    gross_profit: float
    operating_expenses: float
    ebit: float
    interest_expense: float
    pretax_income: float
    tax_expense: float
    net_income: float

    weighted_average_shares: float
    eps: float
    operating_margin: float
    gross_margin: float
    fraud_flag: bool


@dataclass
class BalanceSheetV2:
    company: str
    period_end: date
    available_at: date
    assets: float
    liabilities: float
    equity: float
    cash: float
    receivables: float
    inventory: float
    pp_e_net: float
    debt: float
    payables: float


@dataclass
class CashFlowV2:
    company: str
    period_end: date
    available_at: date
    operating_cf: float
    investing_cf: float
    financing_cf: float
    depreciation: float
    capex: float
    dividends: float
    net_change_in_cash: float


@dataclass
class FilingEventV2:
    company: str
    kind: str  # 10-K | 10-Q
    filed_at: date
    available_at: date
    period_end: date
    filing_id: str
    version_id: str
    version: int
    payload_sha256: str


@dataclass
class EarningsEventV2:
    company: str
    call_at: date
    period_end: date
    revenue: float
    net_income: float
    guidance_margin: float | None
    guidance_met: bool | None


@dataclass
class EstimateRowV2:
    company: str
    metric: str
    period: str
    source: str
    estimate: float
    issued_at: date
    revised_at: date


@dataclass
class PriceRowV2:
    company: str
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class RevisionV2:
    company: str
    metric: str
    period: str
    previous_value: float
    revised_value: float
    original_available_at: date
    revised_available_at: date
    reason: str


@dataclass
class EventV2:
    """Timed observable event used for PIT ordering and news rendering."""

    at: date
    company: str | None
    kind: str
    payload: dict[str, float | int | str | bool]


@dataclass
class WorldOutcomeV2:
    """Complete deterministic world: public + hidden + causal truth."""

    params: EconomyParamsV2
    interventions: tuple[InterventionV2, ...]
    world_id: str
    seed: int
    macro: list[MacroStateV2]
    companies: list[dict[str, str]]
    latents: dict[str, list[LatentAtV2]]
    quarters: list[QuarterRowV2]
    balance_sheets: list[BalanceSheetV2]
    cash_flows: list[CashFlowV2]
    filings: list[FilingEventV2]
    earnings: list[EarningsEventV2]
    estimates: list[EstimateRowV2]
    prices: list[PriceRowV2]
    revisions: list[RevisionV2]
    events: list[EventV2]
    defaults: list[dict[str, str | float]] = field(default_factory=list)
    fraud_windows: list[dict[str, str | bool]] = field(default_factory=list)
    initial_shares: dict[str, int] = field(default_factory=dict)
    accounting: dict[str, CompanyAccountingV1] = field(default_factory=dict)
    rng_namespace: str = NAMESPACE_VERSION
    rng_transform_versions: dict[str, str] = field(default_factory=lambda: dict(TRANSFORM_VERSIONS))
    stream_registry: list[dict[str, str]] = field(default_factory=list)
    rng_world_id: str = ""


# --------------------------------------------------------------------------- #
# Sector profiles
# --------------------------------------------------------------------------- #

_SECTORS: dict[str, dict[str, float]] = {
    "Technology": {
        "gross_margin": 0.62,
        "opex_rate": 0.32,
        "capital_intensity": 0.5,
        "demand_beta": 1.4,
        "ar_days": 65.0,
        "inv_days": 45.0,
    },
    "Industrials": {
        "gross_margin": 0.34,
        "opex_rate": 0.20,
        "capital_intensity": 0.9,
        "demand_beta": 1.1,
        "ar_days": 55.0,
        "inv_days": 85.0,
    },
    "Healthcare": {
        "gross_margin": 0.68,
        "opex_rate": 0.42,
        "capital_intensity": 0.6,
        "demand_beta": 0.6,
        "ar_days": 50.0,
        "inv_days": 60.0,
    },
    "Consumer": {
        "gross_margin": 0.42,
        "opex_rate": 0.26,
        "capital_intensity": 0.7,
        "demand_beta": 0.8,
        "ar_days": 35.0,
        "inv_days": 75.0,
    },
    "Energy": {
        "gross_margin": 0.38,
        "opex_rate": 0.14,
        "capital_intensity": 1.3,
        "demand_beta": 1.2,
        "ar_days": 40.0,
        "inv_days": 55.0,
    },
}

_REGIME_DEMAND: dict[str, float] = {
    "contraction": -0.030,
    "slow": -0.010,
    "trend": 0.000,
    "expansion": 0.015,
}
_REGIME_RATE: dict[str, float] = {
    "contraction": -0.010,
    "slow": -0.002,
    "trend": 0.000,
    "expansion": 0.004,
}


def _quarter_end(year: int, month: int) -> date:
    if month <= 3:
        return date(year, 3, 31)
    if month <= 6:
        return date(year, 6, 30)
    if month <= 9:
        return date(year, 9, 30)
    return date(year, 12, 31)


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def _numeric_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def _state_float(state: dict[str, object], key: str) -> float:
    return _numeric_float(state[key], key)


def _state_int(state: dict[str, object], key: str) -> int:
    value = state[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _sector_params(sector: str) -> dict[str, float]:
    try:
        return _SECTORS[sector]
    except KeyError as exc:  # pragma: no cover - guarded by roster build
        raise ValueError(f"unknown sector: {sector}") from exc


# --------------------------------------------------------------------------- #
# Roster
# --------------------------------------------------------------------------- #


def _company_entity_id(ticker: str) -> str:
    """Return the stable post-construction identity used by company streams."""
    return f"{COMPANY_ENTITY_PREFIX}{ticker}"


def _roster_stream(context: SemanticRNG, slot: int) -> SemanticStream:
    return context.stream(f"ROSTER:{slot:03d}", "roster")


def _ticker_candidate(stream: SemanticStream, attempt: int, letters: str) -> str:
    """Build one four-letter candidate from stable retry coordinates."""
    return "".join(
        letters[stream.randint("ticker_letter", 0, len(letters) - 1, 0, attempt * 4 + offset)]
        for offset in range(4)
    )


def _validate_company_count(company_count: int) -> None:
    if not 1 <= company_count <= 10_000:
        raise ValueError("company_count must be in [1, 10000]")


def _validated_roster(roster: list[dict[str, str]], company_count: int | None) -> list[dict[str, str]]:
    if not roster:
        raise ValueError("company roster must not be empty")
    if len(roster) > 10_000:
        raise ValueError("company roster must contain at most 10000 companies")
    if company_count is not None and len(roster) != company_count:
        raise ValueError("company_count does not match the supplied roster")

    validated: list[dict[str, str]] = []
    seen_tickers: set[str] = set()
    for position, company in enumerate(roster):
        if not isinstance(company, dict):
            raise ValueError(f"roster entry {position} must be an object")
        missing = {"ticker", "name", "sector"} - company.keys()
        if missing:
            raise ValueError(f"roster entry {position} is missing fields: {sorted(missing)}")
        normalized = {key: company[key] for key in ("ticker", "name", "sector")}
        if any(not isinstance(value, str) or not value.strip() for value in normalized.values()):
            raise ValueError(f"roster entry {position} fields must be non-empty strings")
        ticker = normalized["ticker"]
        if ticker in seen_tickers:
            raise ValueError(f"company roster ticker is duplicated: {ticker}")
        _sector_params(normalized["sector"])
        seen_tickers.add(ticker)
        validated.append(normalized)
    return validated


def _generate_roster(context: SemanticRNG, company_count: int) -> list[dict[str, str]]:
    _validate_company_count(company_count)
    roster: list[dict[str, str]] = []
    used_tickers: set[str] = set()
    sector_names = sorted(_SECTORS)
    name_roots = ("Aurora", "Borealis", "Cinder", "Dynamo", "Ember", "Fathom", "Halcyon", "Ionix")
    name_suffixes = ("Systems", "Industries", "Group", "Corp", "Labs", "Works")
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for slot in range(company_count):
        stream = _roster_stream(context, slot)
        sector = stream.pick("sector", sector_names, 0)
        attempt = 0
        while True:
            ticker = _ticker_candidate(stream, attempt, letters)
            if ticker not in used_tickers:
                break
            attempt += 1
            if attempt > 10_000:
                raise RuntimeError(f"could not find a unique ticker for roster slot {slot:03d}")
        used_tickers.add(ticker)
        name = f"{stream.pick('name_root', name_roots, 0)} {stream.pick('name_suffix', name_suffixes, 0)}"
        roster.append({"ticker": ticker, "name": name, "sector": sector})
    return roster


def build_economy(
    params: EconomyParamsV2,
    world_id: str = "fuzzer-000000",
    company_count: int = 24,
    *,
    semantic_rng: SemanticRNG | None = None,
) -> tuple[int, list[dict[str, str]]]:
    """Deterministically derive a roster using stable ``ROSTER:<slot>`` identities."""
    params.validate()
    context = semantic_rng or SemanticRNG(world_id, params.seed)
    return params.seed, _generate_roster(context, company_count)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class EconomyEngineV2:
    """Deterministic fundamental economy for a panel of synthetic companies."""

    def __init__(
        self,
        params: EconomyParamsV2,
        interventions: tuple[InterventionV2, ...] = (),
        world_id: str = "fuzzer-000000",
        *,
        roster: list[dict[str, str]] | None = None,
        company_count: int | None = None,
    ) -> None:
        self.params = params.validate()
        self.interventions = tuple(sorted(interventions, key=lambda iv: (iv.company, iv.variable, iv.start)))
        self.world_id = world_id
        self._rng = SemanticRNG(world_id, self.params.seed)
        if roster is None:
            _, self.roster = build_economy(
                self.params,
                world_id=self.world_id,
                company_count=24 if company_count is None else company_count,
                semantic_rng=self._rng,
            )
        else:
            self.roster = _validated_roster(roster, company_count)
        self._entity_ids = {
            company["ticker"]: _company_entity_id(company["ticker"]) for company in self.roster
        }
        self._initial_rngs = {
            ticker: self._rng.stream(entity_id, "initial_state")
            for ticker, entity_id in self._entity_ids.items()
        }
        self._company_rngs = {
            ticker: self._rng.stream(entity_id, "operations")
            for ticker, entity_id in self._entity_ids.items()
        }
        self._est_rngs = {
            ticker: self._rng.stream(entity_id, "estimates") for ticker, entity_id in self._entity_ids.items()
        }
        self._price_rngs = {
            ticker: self._rng.stream(entity_id, "price") for ticker, entity_id in self._entity_ids.items()
        }
        self._macro_rng = self._rng.stream(WORLD, "macro")
        self._industry_rngs = {
            sector: self._rng.stream(f"{SECTOR_ENTITY_PREFIX}{sector}", "industry")
            for sector in sorted({company["sector"] for company in self.roster})
        }

    # ------------------------------------------------------------------ #
    # interventions
    # ------------------------------------------------------------------ #

    def _clamp(self, ticker: str, variable: str, when: date) -> float | None:
        # stacked interventions: when several are active for the same
        # (company, variable), the latest-starting one wins
        best: InterventionV2 | None = None
        for iv in self.interventions:
            if iv.company != ticker or iv.variable != variable:
                continue
            if when < iv.start:
                continue
            if iv.end is not None and when > iv.end:
                continue
            if best is None or iv.start > best.start:
                best = iv
        return best.value if best is not None else None

    # ------------------------------------------------------------------ #
    # macro
    # ------------------------------------------------------------------ #

    def _macro_path(self) -> list[MacroStateV2]:
        p = self.params
        rng = self._macro_rng
        regime = "trend"
        out: list[MacroStateV2] = []
        for q in range(p.years * 4):
            when_q = _quarter_end(p.start_year + q // 4, (q % 4) * 3 + 3)
            year_float = q / 4.0
            gdp = p.gdp_trend + p.gdp_cycle_amplitude * (
                0.7 * _sin(2.0 * _pi * year_float / p.gdp_cycle_period_years)
                + 0.3 * _sin(2.0 * _pi * year_float / (p.gdp_cycle_period_years / 2.7))
            )
            inflation = p.inflation_trend + p.inflation_amplitude * _sin(
                2.0 * _pi * year_float / p.inflation_period_years
            )
            if q > 0 and rng.uniform("regime_transition_probability", q) < 1.0 / max(
                p.regime_persistence_years * 4.0, 1.0
            ):
                others = [s for s in p.regime_states if s != regime]
                regime = rng.pick("regime_transition", others, q)
            gdp += _REGIME_DEMAND[regime]
            rate_shock_bps = self._clamp(WORLD, "rate_shock_bps", when_q)
            rate_delta = rate_shock_bps / 10_000.0 if rate_shock_bps is not None else 0.0
            policy_rate = max(
                0.0005,
                p.rates_start + rate_delta + 0.5 * (inflation - p.inflation_trend) + _REGIME_RATE[regime],
            )
            credit_index = max(0.2, 1.0 + 6.0 * max(0.0, -gdp) + 0.4 * (policy_rate - p.rates_start) / 0.03)
            out.append(
                MacroStateV2(
                    date=_quarter_end(p.start_year + q // 4, (q % 4) * 3 + 3),
                    gdp_growth=gdp,
                    inflation=inflation,
                    policy_rate=policy_rate,
                    regime=regime,
                    credit_index=credit_index,
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # main loop — quarter-outer so macro/industry advance once per quarter
    # ------------------------------------------------------------------ #

    def run(self) -> WorldOutcomeV2:
        p = self.params
        macro_path = self._macro_path()

        quarters: list[QuarterRowV2] = []
        balance_sheets: list[BalanceSheetV2] = []
        cash_flows: list[CashFlowV2] = []
        filings: list[FilingEventV2] = []
        earnings: list[EarningsEventV2] = []
        estimates: list[EstimateRowV2] = []
        prices: list[PriceRowV2] = []
        revisions: list[RevisionV2] = []
        events: list[EventV2] = []
        defaults: list[dict[str, str | float]] = []
        fraud_windows: list[dict[str, str | bool]] = []
        latents_all: dict[str, list[LatentAtV2]] = {}

        sector_state: dict[str, dict[str, float]] = {}

        # per-company dynamic state
        st: dict[str, dict[str, object]] = {}
        initial_shares: dict[str, int] = {}
        accounting: dict[str, CompanyAccountingV1] = {}
        opening_rate = (
            macro_path[0].policy_rate * (1.0 + macro_path[0].credit_index * 0.5)
            if macro_path
            else p.rates_start
        )
        for company in self.roster:
            ticker = company["ticker"]
            sector = company["sector"]
            sp = _sector_params(sector)
            rng = self._initial_rngs[ticker]

            size = rng.lognormal("size", 0.0, p.size_dispersion, 0)
            base_revenue = 250e6 * size
            equity0 = base_revenue * (0.8 + 0.4 * rng.uniform("equity_fraction", 0))
            leverage0 = max(0.05, 0.35 + p.leverage_dispersion * rng.normal("leverage", 0))
            revenue_q = base_revenue / 4.0
            debt = max(0.0, equity0 * leverage0)
            cash = equity0 * 0.10
            pp_e_net = base_revenue * sp["capital_intensity"] * 0.9
            receivables = revenue_q * sp["ar_days"] / 90.0
            inventory = revenue_q * sp["inv_days"] / 90.0
            payables = revenue_q * p.days_payable / 90.0
            retained = max(
                equity0 * 0.05,
                equity0 - (debt - cash) - pp_e_net - receivables - inventory + payables,
            )
            shares = max(
                1, int(round((base_revenue / 25.0) * rng.uniform_range("initial_shares", 0.7, 1.3, 0)))
            )
            initial_shares[ticker] = shares
            accounting[ticker] = CompanyAccountingV1(
                ticker=ticker,
                initial_shares=shares,
                cash_target=cash,
                ar_target=receivables,
                inventory_target=inventory,
                ppe_target=pp_e_net,
                ap_target=payables,
                debt_target=debt,
                retained_target=retained,
                depreciation_rate=p.depreciation_rate,
                interest_rate=opening_rate,
                tax_rate=p.tax_rate,
                payout_ratio=p.payout_ratio,
                min_cash_buffer_ratio=p.min_cash_buffer,
                sector=sector,
                horizon_quarters=len(macro_path),
                opening_date=date(p.start_year - 1, 12, 31),
            )
            st[ticker] = {
                "revenue_q": revenue_q,
                "growth": 0.06 + p.growth_dispersion * rng.normal("growth", 0),
                "growth0": 0.06 + p.growth_dispersion * rng.normal("growth0", 0),
                "gross_margin0": sp["gross_margin"] + p.margin_dispersion * rng.normal("gross_margin0", 0),
                "gross_margin": sp["gross_margin"] + p.margin_dispersion * rng.normal("gross_margin", 0),
                "shares": shares,
                "demand_index": 1.0,
                "input_cost_index": 1.0,
                "labor_cost_index": 1.0,
                "pricing_power": _clamp01(0.5 + 0.3 * rng.normal("pricing_power", 0)),
                "market_share": _clamp01(0.02 + 0.08 * abs(rng.normal("market_share", 0))),
                "mgmt_quality": _clamp01(0.5 + 0.25 * rng.normal("management_quality", 0)),
                "debt_stress": 0.2,
                "liquidity": _clamp01(0.5 + 0.2 * rng.normal("liquidity", 0)),
                "fraud_open": False,
                "fraud_start": None,
                "active": True,
                "guidance": None,
                "prior_close": max(0.5, equity0 / shares * 1.8),
                "prior_est": None,
            }

        for qi, macro in enumerate(macro_path):
            when = macro.date
            fy = p.start_year + qi // 4
            fq = (qi % 4) + 1

            # ---- industry dynamics (once per sector per quarter) -------- #
            for sector in sorted({c["sector"] for c in self.roster}):
                irng = self._industry_rngs[sector]
                ist = sector_state.setdefault(sector, {"momentum": 0.0, "shock": 0.0, "intensity": 0.5})
                ist["momentum"] = (
                    0.7 * ist["momentum"]
                    + 0.3 * irng.normal("momentum_shock", qi) * p.industry_growth_dispersion
                )
                ist["shock"] = max(0.0, ist["shock"] * 0.5)
                if irng.uniform("shock_event", qi) < 0.04 * p.shock_intensity:
                    ist["shock"] = abs(irng.normal("shock_magnitude", qi)) * 0.5
                ist["intensity"] = _clamp01(
                    0.9 * ist["intensity"] + 0.1 * (0.5 + 0.2 * irng.normal("competitive_intensity", qi))
                )

            # ---- companies ---------------------------------------------- #
            for company in self.roster:
                ticker = company["ticker"]
                sector = company["sector"]
                sp = _sector_params(sector)
                if not bool(st[ticker]["active"]):
                    continue
                rng = self._company_rngs[ticker]
                s = st[ticker]

                ist = sector_state[sector]

                # ---- interventions (do-clamps) --------------------------- #
                demand_clamp = self._clamp(ticker, "demand_index", when)
                fraud_clamp = self._clamp(ticker, "fraud_propensity", when)
                supplier_failed = self._clamp(ticker, "supplier_failure", when) == 1.0

                # ---- macro -> company ------------------------------------ #
                prior_demand = _state_float(s, "demand_index")
                demand_noise = rng.normal("demand_noise", qi) * 0.04
                if demand_clamp is not None:
                    demand_index = demand_clamp
                else:
                    demand_index = prior_demand * (
                        1.0
                        + 0.30 * macro.gdp_growth * sp["demand_beta"]
                        + 0.50 * ist["momentum"] * sp["demand_beta"]
                        - 0.60 * ist["shock"]
                        + demand_noise
                    )
                    demand_index = max(0.05, demand_index)
                s["demand_index"] = demand_index

                input_cost_index = max(
                    0.2,
                    _state_float(s, "input_cost_index")
                    * (
                        1.0
                        + 0.55 * (macro.inflation - p.inflation_trend)
                        + rng.normal("input_cost_noise", qi) * 0.015
                    ),
                )
                labor_cost_index = max(
                    0.2,
                    _state_float(s, "labor_cost_index")
                    * (
                        1.0
                        + 0.45 * (macro.inflation - p.inflation_trend)
                        + rng.normal("labor_cost_noise", qi) * 0.010
                    ),
                )
                s["input_cost_index"] = input_cost_index
                s["labor_cost_index"] = labor_cost_index
                interest_rate = macro.policy_rate * (1.0 + macro.credit_index * 0.5)

                # ---- operations ------------------------------------------ #
                growth = 0.75 * _state_float(s, "growth") + 0.25 * (
                    _state_float(s, "growth0") * (0.6 + 0.8 * _state_float(s, "mgmt_quality"))
                    + 0.35 * (macro.gdp_growth - p.gdp_trend) * sp["demand_beta"]
                    - 0.30 * (ist["intensity"] - 0.5)
                    + rng.normal("growth_noise", qi) * 0.05
                )
                growth = max(-0.45, min(0.60, growth))
                s["growth"] = growth
                demand_effect = (demand_index / prior_demand) - 1.0 if prior_demand > 0 else 0.0
                revenue_q = max(1e6, _state_float(s, "revenue_q") * (1.0 + growth + demand_effect))
                s["revenue_q"] = revenue_q

                # ---- fraud bookkeeping ------------------------------------ #
                # always draw, then clamp: interventions must not perturb the
                # random draw sequence, only the state they intervene on
                fraud_draw = _clamp01(0.5 + 0.15 * rng.normal("fraud_propensity", qi))
                fraud_prop = fraud_clamp if fraud_clamp is not None else fraud_draw
                distress_proxy = (
                    _state_float(s, "debt_stress") * 0.6 + (1.0 - _state_float(s, "liquidity")) * 0.4
                )
                # organic fraud is RARE: either an extreme latent propensity,
                # or high propensity under real distress (desperate + dishonest).
                # Interventions clamp the propensity and bypass the gate.
                want_open = fraud_prop > 0.95 or (fraud_prop > 0.72 and distress_proxy > 0.55)
                if not bool(s["fraud_open"]) and want_open:
                    s["fraud_open"] = True
                    s["fraud_start"] = when
                if bool(s["fraud_open"]) and fraud_prop < 0.35:
                    s["fraud_open"] = False
                    fraud_windows.append(
                        {
                            "company": ticker,
                            "start": str(s["fraud_start"]),
                            "end": when.isoformat(),
                        }
                    )
                    s["fraud_start"] = None
                inflating = bool(s["fraud_open"]) and not supplier_failed

                # ---- accounting ------------------------------------------- #
                cost_push = 1.0 + 0.5 * (input_cost_index - 1.0) + 0.3 * (labor_cost_index - 1.0)
                if supplier_failed:
                    cost_push *= 1.25
                price_realization = 1.0 + 0.7 * (_state_float(s, "pricing_power") - 0.5) * (
                    input_cost_index - 1.0
                )
                gross_margin = _clamp01(
                    _state_float(s, "gross_margin0") * (price_realization / max(cost_push, 0.5))
                    - 0.10 * (ist["intensity"] - 0.5)
                    + 0.02 * (_state_float(s, "mgmt_quality") - 0.5)
                    + rng.normal("gross_margin_noise", qi) * 0.008
                )
                s["gross_margin"] = gross_margin
                opex = revenue_q * sp["opex_rate"] * (1.0 + 0.10 * (labor_cost_index - 1.0))
                capex = revenue_q * p.capex_rate * (0.7 + 0.6 * sp["capital_intensity"])
                accounting_result = accounting[ticker].apply_quarter(
                    sector=sector,
                    period=qi + 1,
                    period_end=when,
                    true_revenue=revenue_q,
                    gross_margin=gross_margin,
                    days_receivable=sp["ar_days"],
                    days_inventory=sp["inv_days"],
                    days_payable=p.days_payable,
                    sga=opex,
                    capex=capex,
                    tax_rate=p.tax_rate,
                    payout_ratio=p.payout_ratio,
                    current_interest_rate=interest_rate,
                    fraud=inflating,
                )
                income = accounting_result.income_statement
                balance = accounting_result.balance_sheet
                cash_flow = accounting_result.cash_flow
                revenue = float(income["revenue"])
                cogs = float(income["cogs"])
                gross_profit = float(income["gross_profit"])
                ebit = float(income["operating_income"])
                interest_expense = float(income["interest"])
                pretax = float(income["pretax_income"])
                tax = float(income["tax_expense"])
                reported_ni = float(income["net_income"])
                eps = float(accounting_result.basic_eps)
                operating_margin = ebit / revenue if revenue else 0.0
                gross_margin_pub = gross_profit / revenue if revenue else 0.0
                depreciation = float(income["depreciation"])
                capex_actual = float(accounting_result.capex)
                dividends_actual = float(accounting_result.dividends)
                net_change_cash = float(accounting_result.net_change_in_cash)
                filing_available = accounting_result.available_date
                call_at = when + timedelta(days=30)

                quarters.append(
                    QuarterRowV2(
                        company=ticker,
                        sector=sector,
                        fiscal_year=fy,
                        fiscal_quarter=fq,
                        period_end=when,
                        available_at=filing_available,
                        revenue=revenue,
                        cogs=cogs,
                        gross_profit=gross_profit,
                        operating_expenses=float(income["sga"]),
                        ebit=ebit,
                        interest_expense=interest_expense,
                        pretax_income=pretax,
                        tax_expense=tax,
                        net_income=reported_ni,
                        weighted_average_shares=float(accounting_result.weighted_average_shares),
                        eps=eps,
                        operating_margin=operating_margin,
                        gross_margin=gross_margin_pub,
                        fraud_flag=inflating,
                    )
                )
                balance_sheets.append(
                    BalanceSheetV2(
                        company=ticker,
                        period_end=when,
                        available_at=filing_available,
                        assets=float(balance["total_assets"]),
                        liabilities=float(balance["total_liabilities"]),
                        equity=float(balance["total_equity"]),
                        cash=float(balance["cash"]),
                        receivables=float(balance["ar"]),
                        inventory=float(balance["inventory"]),
                        pp_e_net=float(balance["ppe_net"]),
                        debt=float(balance["debt"]),
                        payables=float(balance["ap"]),
                    )
                )
                cash_flows.append(
                    CashFlowV2(
                        company=ticker,
                        period_end=when,
                        available_at=filing_available,
                        operating_cf=float(cash_flow["operating"]),
                        investing_cf=float(cash_flow["investing"]),
                        financing_cf=float(cash_flow["financing"]),
                        depreciation=depreciation,
                        capex=capex_actual,
                        dividends=dividends_actual,
                        net_change_in_cash=net_change_cash,
                    )
                )
                filings.append(
                    FilingEventV2(
                        company=ticker,
                        kind=accounting_result.filing.form.value,
                        filed_at=filing_available,
                        available_at=filing_available,
                        period_end=when,
                        filing_id=accounting_result.filing.filing_id,
                        version_id=accounting_result.filing.version_id,
                        version=accounting_result.filing.version,
                        payload_sha256=accounting_result.filing.payload_sha256,
                    )
                )

                # ---- distress / default ------------------------------------ #
                liquidity = _clamp01(
                    0.9 * _state_float(s, "liquidity")
                    + 0.1 * (float(balance["cash"]) / max(float(balance["total_assets"]), 1.0) / 0.10)
                )
                debt_stress = _clamp01(
                    0.85 * _state_float(s, "debt_stress")
                    + 0.15
                    * (
                        (float(balance["debt"]) / max(float(balance["total_equity"]), 1.0)) / 2.0
                        + max(0.0, -float(cash_flow["operating"])) / max(revenue, 1.0) * 4.0
                    )
                )
                s["liquidity"] = liquidity
                s["debt_stress"] = debt_stress
                if debt_stress > 1.0 - p.distress_threshold or liquidity < p.min_cash_buffer * 10:
                    defaults.append(
                        {"company": ticker, "at": when.isoformat(), "debt_stress": round(debt_stress, 6)}
                    )
                    if bool(s["fraud_open"]):
                        s["fraud_open"] = False
                        fraud_windows.append(
                            {"company": ticker, "start": str(s["fraud_start"]), "end": when.isoformat()}
                        )
                        s["fraud_start"] = None
                    s["active"] = False
                    continue

                # ---- earnings call, guidance, estimates, prices -------------- #
                pending_guidance = s["guidance"]
                guidance_met: bool | None = (
                    gross_margin >= _numeric_float(pending_guidance, "guidance") * 0.98
                    if pending_guidance is not None
                    else None
                )
                next_guidance = gross_margin * (1.0 + 0.02 * (_state_float(s, "mgmt_quality") - 0.5))
                s["guidance"] = next_guidance

                earnings.append(
                    EarningsEventV2(
                        company=ticker,
                        call_at=call_at,
                        period_end=when,
                        revenue=revenue,
                        net_income=reported_ni,
                        guidance_margin=(
                            _numeric_float(pending_guidance, "guidance")
                            if pending_guidance is not None
                            else None
                        ),
                        guidance_met=guidance_met,
                    )
                )
                events.append(
                    EventV2(
                        at=call_at,
                        company=ticker,
                        kind="earnings_call",
                        payload={
                            "revenue": round(revenue, 2),
                            "net_income": round(reported_ni, 2),
                            "gross_margin": round(gross_margin, 6),
                            "guidance_met": bool(guidance_met) if guidance_met is not None else False,
                        },
                    )
                )
                events.append(
                    EventV2(
                        at=filing_available,
                        company=ticker,
                        kind="filing",
                        payload={
                            "form": accounting_result.filing.form.value,
                            "period_end": when.isoformat(),
                        },
                    )
                )

                # analyst estimate issued BEFORE the print (true PIT)
                est_rng = self._est_rngs[ticker]
                period_label = f"{fy}Q{fq}"
                had_prior_est = s["prior_est"] is not None
                prior_est = s["prior_est"]
                prior_est = (
                    _numeric_float(prior_est, "prior_est")
                    if had_prior_est
                    else revenue_q * (1.0 + p.analyst_bias)
                )
                est_now = (
                    prior_est
                    * (1.0 + p.analyst_bias)
                    * (1.0 + est_rng.normal("revenue_error", qi) * p.analyst_noise)
                )
                issued_at = when - timedelta(days=45)
                estimates.append(
                    EstimateRowV2(
                        company=ticker,
                        metric="revenue",
                        period=period_label,
                        source="consensus-v0",
                        estimate=est_now,
                        issued_at=issued_at,
                        revised_at=call_at + timedelta(days=1),
                    )
                )
                if had_prior_est:
                    revisions.append(
                        RevisionV2(
                            company=ticker,
                            metric="revenue",
                            period=period_label,
                            previous_value=prior_est,
                            revised_value=est_now,
                            original_available_at=when - timedelta(days=135),
                            revised_available_at=call_at + timedelta(days=1),
                            reason="post-earnings revision",
                        )
                    )
                s["prior_est"] = revenue  # next quarter's anchor is this actual

                # Quarter-end price uses only state available at the session timestamp.
                # Current-quarter earnings and guidance are published after this row.
                price_rng = self._price_rngs[ticker]
                share_count = _state_int(s, "shares")
                drift = 0.10 * growth / 4.0
                ret = drift + price_rng.normal("return_noise", qi) * p.price_noise
                prev_close = _state_float(s, "prior_close")
                close = max(0.5, prev_close * (1.0 + ret))
                prices.append(
                    PriceRowV2(
                        company=ticker,
                        session=when,
                        open=prev_close,
                        high=max(prev_close, close) * 1.005,
                        low=min(prev_close, close) * 0.995,
                        close=close,
                        volume=max(
                            1.0,
                            share_count * 0.001 * price_rng.uniform_range("volume_scale", 0.5, 1.5, qi),
                        ),
                    )
                )
                s["prior_close"] = close

                # latent snapshot (hidden)
                latents_all.setdefault(ticker, []).append(
                    LatentAtV2(
                        date=when,
                        values={
                            "demand_index": demand_index,
                            "pricing_power": _state_float(s, "pricing_power"),
                            "market_share": _state_float(s, "market_share"),
                            "input_cost_index": input_cost_index,
                            "labor_cost_index": labor_cost_index,
                            "debt_stress": debt_stress,
                            "liquidity": liquidity,
                            "management_quality": _state_float(s, "mgmt_quality"),
                            "fraud_propensity": fraud_prop,
                            "competitive_intensity": ist["intensity"],
                        },
                    )
                )

        # close any fraud windows still open at the final quarter
        last_day = macro_path[-1].date if macro_path else date(p.start_year, 12, 31)
        for company in self.roster:
            ticker = company["ticker"]
            s = st[ticker]
            if bool(s["fraud_open"]) and s["fraud_start"] is not None:
                fraud_windows.append(
                    {"company": ticker, "start": str(s["fraud_start"]), "end": last_day.isoformat()}
                )

        return WorldOutcomeV2(
            params=p,
            interventions=self.interventions,
            world_id=self.world_id,
            seed=p.seed,
            macro=macro_path,
            companies=self.roster,
            latents=latents_all,
            quarters=quarters,
            balance_sheets=balance_sheets,
            cash_flows=cash_flows,
            filings=filings,
            earnings=earnings,
            estimates=estimates,
            prices=prices,
            revisions=revisions,
            events=events,
            defaults=defaults,
            fraud_windows=fraud_windows,
            initial_shares=initial_shares,
            accounting=accounting,
            rng_namespace=NAMESPACE_VERSION,
            rng_transform_versions=dict(TRANSFORM_VERSIONS),
            stream_registry=self._rng.manifest(),
            rng_world_id=self._rng.rng_world_id,
        )


def run_economy(
    params: EconomyParamsV2,
    interventions: tuple[InterventionV2, ...] = (),
    world_id: str = "fuzzer-000000",
    *,
    roster: list[dict[str, str]] | None = None,
    company_count: int | None = None,
) -> WorldOutcomeV2:
    """Build and run a deterministic V2 world in one call."""
    return EconomyEngineV2(
        params,
        interventions,
        world_id,
        roster=roster,
        company_count=company_count,
    ).run()
