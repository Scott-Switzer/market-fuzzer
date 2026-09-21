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

Balance-sheet identity holds exactly every quarter:
    assets == liabilities + equity
and a separate `plug` field measures the reconciliation gap between the
cash-flow statement and the cash carried on the balance sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from math import pi as _pi
from math import sin as _sin

from app.world.rng import derive_stream

WORLD = "WORLD"

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
    equity_check_residual: float
    plug: float


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


# --------------------------------------------------------------------------- #
# Sector profiles
# --------------------------------------------------------------------------- #

_SECTORS: dict[str, dict[str, float]] = {
    "Technology": {
        "gross_margin": 0.62, "opex_rate": 0.32, "capital_intensity": 0.5,
        "demand_beta": 1.4, "ar_days": 65.0, "inv_days": 45.0,
    },
    "Industrials": {
        "gross_margin": 0.34, "opex_rate": 0.20, "capital_intensity": 0.9,
        "demand_beta": 1.1, "ar_days": 55.0, "inv_days": 85.0,
    },
    "Healthcare": {
        "gross_margin": 0.68, "opex_rate": 0.42, "capital_intensity": 0.6,
        "demand_beta": 0.6, "ar_days": 50.0, "inv_days": 60.0,
    },
    "Consumer": {
        "gross_margin": 0.42, "opex_rate": 0.26, "capital_intensity": 0.7,
        "demand_beta": 0.8, "ar_days": 35.0, "inv_days": 75.0,
    },
    "Energy": {
        "gross_margin": 0.38, "opex_rate": 0.14, "capital_intensity": 1.3,
        "demand_beta": 1.2, "ar_days": 40.0, "inv_days": 55.0,
    },
}

_REGIME_DEMAND: dict[str, float] = {
    "contraction": -0.030, "slow": -0.010, "trend": 0.000, "expansion": 0.015,
}
_REGIME_RATE: dict[str, float] = {
    "contraction": -0.010, "slow": -0.002, "trend": 0.000, "expansion": 0.004,
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


def _sector_params(sector: str) -> dict[str, float]:
    try:
        return _SECTORS[sector]
    except KeyError as exc:  # pragma: no cover - guarded by roster build
        raise ValueError(f"unknown sector: {sector}") from exc


# --------------------------------------------------------------------------- #
# Roster
# --------------------------------------------------------------------------- #


def build_economy(params: EconomyParamsV2) -> tuple[int, list[dict[str, str]]]:
    """Deterministically derive the company roster from the seed."""
    params.validate()
    roster: list[dict[str, str]] = []
    sector_names = sorted(_SECTORS)
    name_roots = ("Aurora", "Borealis", "Cinder", "Dynamo", "Ember", "Fathom", "Halcyon", "Ionix")
    name_suffixes = ("Systems", "Industries", "Group", "Corp", "Labs", "Works")
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i in range(24):
        stream = derive_stream(params.seed, f"company:{i}")
        sector = sector_names[stream.randint(0, len(sector_names) - 1)]
        ticker = "".join(letters[stream.randint(0, 25)] for _ in range(4))
        name = (
            f"{name_roots[stream.randint(0, len(name_roots) - 1)]} "
            f"{name_suffixes[stream.randint(0, len(name_suffixes) - 1)]}"
        )
        roster.append({"ticker": ticker, "name": name, "sector": sector})
    return params.seed, roster


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
    ) -> None:
        self.params = params.validate()
        self.interventions = tuple(
            sorted(interventions, key=lambda iv: (iv.company, iv.variable, iv.start))
        )
        self.world_id = world_id
        _, roster = build_economy(params)
        self.roster = roster
        self._macro_rng = derive_stream(params.seed, "macro")
        self._industry_rng = derive_stream(params.seed, "industry")
        self._news_rng = derive_stream(params.seed, "news")
        self._company_rngs = {c["ticker"]: derive_stream(params.seed, f"latent:{c['ticker']}") for c in roster}
        self._est_rngs = {c["ticker"]: derive_stream(params.seed, f"est:{c['ticker']}") for c in roster}
        self._price_rngs = {c["ticker"]: derive_stream(params.seed, f"price:{c['ticker']}") for c in roster}

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
            if q > 0 and rng.uniform() < 1.0 / max(p.regime_persistence_years * 4.0, 1.0):
                others = [s for s in p.regime_states if s != regime]
                regime = others[rng.randint(0, len(others) - 1)]
            gdp += _REGIME_DEMAND[regime]
            rate_shock_bps = self._clamp(WORLD, "rate_shock_bps", when_q)
            rate_delta = rate_shock_bps / 10_000.0 if rate_shock_bps is not None else 0.0
            policy_rate = max(
                0.0005,
                p.rates_start + rate_delta + 0.5 * (inflation - p.inflation_trend) + _REGIME_RATE[regime],
            )
            credit_index = max(
                0.2, 1.0 + 6.0 * max(0.0, -gdp) + 0.4 * (policy_rate - p.rates_start) / 0.03
            )
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
        st: dict[str, dict[str, float | object]] = {}
        for company in self.roster:
            ticker = company["ticker"]
            sector = company["sector"]
            sp = _sector_params(sector)
            rng = self._company_rngs[ticker]

            size = rng.lognormal(0.0, p.size_dispersion)
            base_revenue = 250e6 * size
            equity0 = base_revenue * (0.8 + 0.4 * rng.uniform())
            leverage0 = max(0.05, 0.35 + p.leverage_dispersion * rng.normal())
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
            shares = max(1.0, round((base_revenue / 25.0) * rng.uniform_range(0.7, 1.3)))
            st[ticker] = {
                "revenue_q": revenue_q,
                "growth": 0.06 + p.growth_dispersion * rng.normal(),
                "growth0": 0.06 + p.growth_dispersion * rng.normal(),
                "gross_margin0": sp["gross_margin"] + p.margin_dispersion * rng.normal(),
                "gross_margin": sp["gross_margin"] + p.margin_dispersion * rng.normal(),
                "shares": max(1.0, round((base_revenue / 25.0) * rng.uniform_range(0.7, 1.3))),
                "debt": debt,
                "cash": cash,
                "pp_e_net": pp_e_net,
                "receivables": receivables,
                "inventory": inventory,
                "payables": payables,
                "retained": retained,
                "demand_index": 1.0,
                "input_cost_index": 1.0,
                "labor_cost_index": 1.0,
                "pricing_power": _clamp01(0.5 + 0.3 * rng.normal()),
                "market_share": _clamp01(0.02 + 0.08 * abs(rng.normal())),
                "mgmt_quality": _clamp01(0.5 + 0.25 * rng.normal()),
                "debt_stress": 0.2,
                "liquidity": _clamp01(0.5 + 0.2 * rng.normal()),
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
            irng = self._industry_rng
            for sector in sorted({c["sector"] for c in self.roster}):
                ist = sector_state.setdefault(sector, {"momentum": 0.0, "shock": 0.0, "intensity": 0.5})
                ist["momentum"] = 0.7 * ist["momentum"] + 0.3 * irng.normal() * p.industry_growth_dispersion
                ist["shock"] = max(0.0, ist["shock"] * 0.5)
                if irng.uniform() < 0.04 * p.shock_intensity:
                    ist["shock"] = abs(irng.normal()) * 0.5
                ist["intensity"] = _clamp01(0.9 * ist["intensity"] + 0.1 * (0.5 + 0.2 * irng.normal()))

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
                supplier_failed = self._clamp(ticker, "supplier_failure", when) is not None

                # ---- macro -> company ------------------------------------ #
                prior_demand = float(s["demand_index"])
                demand_index = (demand_clamp if demand_clamp is not None else prior_demand) * (
                    1.0
                    + 0.30 * macro.gdp_growth * sp["demand_beta"]
                    + 0.50 * ist["momentum"] * sp["demand_beta"]
                    - 0.60 * ist["shock"]
                    + rng.normal() * 0.04
                )
                demand_index = max(0.05, demand_index)
                s["demand_index"] = demand_index

                input_cost_index = max(
                    0.2,
                    float(s["input_cost_index"])
                    * (1.0 + 0.55 * (macro.inflation - p.inflation_trend) + rng.normal() * 0.015),
                )
                labor_cost_index = max(
                    0.2,
                    float(s["labor_cost_index"])
                    * (1.0 + 0.45 * (macro.inflation - p.inflation_trend) + rng.normal() * 0.010),
                )
                s["input_cost_index"] = input_cost_index
                s["labor_cost_index"] = labor_cost_index
                interest_rate = macro.policy_rate * (1.0 + macro.credit_index * 0.5)

                # ---- operations ------------------------------------------ #
                growth = 0.75 * float(s["growth"]) + 0.25 * (
                    float(s["growth0"]) * (0.6 + 0.8 * float(s["mgmt_quality"]))
                    + 0.35 * (macro.gdp_growth - p.gdp_trend) * sp["demand_beta"]
                    - 0.30 * (ist["intensity"] - 0.5)
                    + rng.normal() * 0.05
                )
                growth = max(-0.45, min(0.60, growth))
                s["growth"] = growth
                demand_effect = (demand_index / prior_demand) - 1.0 if prior_demand > 0 else 0.0
                revenue_q = max(1e6, float(s["revenue_q"]) * (1.0 + growth + demand_effect))
                s["revenue_q"] = revenue_q

                # ---- fraud bookkeeping ------------------------------------ #
                # always draw, then clamp: interventions must not perturb the
                # random draw sequence, only the state they intervene on
                fraud_draw = _clamp01(0.5 + 0.15 * rng.normal())
                fraud_prop = fraud_clamp if fraud_clamp is not None else fraud_draw
                distress_proxy = float(s["debt_stress"]) * 0.6 + (1.0 - float(s["liquidity"])) * 0.4
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

                # ---- P&L --------------------------------------------------- #
                cost_push = 1.0 + 0.5 * (input_cost_index - 1.0) + 0.3 * (labor_cost_index - 1.0)
                if supplier_failed:
                    cost_push *= 1.25
                price_realization = 1.0 + 0.7 * (float(s["pricing_power"]) - 0.5) * (input_cost_index - 1.0)
                gross_margin = _clamp01(
                    float(s["gross_margin0"])
                    * (price_realization / max(cost_push, 0.5))
                    - 0.10 * (ist["intensity"] - 0.5)
                    + 0.02 * (float(s["mgmt_quality"]) - 0.5)
                    + rng.normal() * 0.008
                )
                s["gross_margin"] = gross_margin

                debt = float(s["debt"])
                interest_expense = debt * interest_rate / 4.0
                opex = revenue_q * sp["opex_rate"] * (1.0 + 0.10 * (labor_cost_index - 1.0))

                # --- true economics (drives cash flow) --------------------- #
                revenue = revenue_q
                cogs = revenue_q * (1.0 - gross_margin)
                true_ebit = (revenue_q - cogs) - opex
                true_pretax = true_ebit - interest_expense
                true_ni = true_pretax - max(0.0, true_pretax) * p.tax_rate

                # --- reported P&L (fraud inflates revenue; still reconciles,
                # like real fraudulent filings — but cash flow stays true, so
                # reported earnings outrun cash generation: the accrual tell) -#
                if inflating:
                    revenue = revenue_q * 1.004
                gross_profit = revenue - cogs
                ebit = gross_profit - opex
                pretax = ebit - interest_expense
                tax = max(0.0, pretax) * p.tax_rate
                reported_ni = pretax - tax
                shares = float(s["shares"])
                eps = reported_ni / shares
                operating_margin = ebit / revenue
                gross_margin_pub = gross_profit / revenue

                # ---- balance sheet / cash flow ------------------------------ #
                receivables = revenue * sp["ar_days"] / 90.0
                inventory = revenue_q * sp["inv_days"] / 90.0
                payables = revenue_q * p.days_payable / 90.0
                depreciation = float(s["pp_e_net"]) * p.depreciation_rate / 4.0
                capex = revenue_q * p.capex_rate * (0.7 + 0.6 * sp["capital_intensity"])
                pp_e_net = max(0.0, float(s["pp_e_net"]) + capex - depreciation)

                cash_target = float(s["cash"]) + true_ni + depreciation - capex
                dividends = max(0.0, reported_ni) * p.payout_ratio
                refinance_need = max(0.0, debt * 0.05 - cash_target * 0.5)
                debt_change = refinance_need - dividends * 0.3
                debt_next = max(0.0, debt + debt_change)
                cash_next_unplugged = cash_target - dividends + debt_change
                cash_next = max(0.0, cash_next_unplugged)
                # plug = financing gap absorbed when cash would go negative;
                # balance identity stays exact because equity absorbs the plug.
                plug = cash_next - cash_next_unplugged

                assets_next = cash_next + receivables + inventory + pp_e_net
                liabilities_next = debt_next + payables
                equity_next = assets_next - liabilities_next
                retained_next = float(s["retained"]) + reported_ni - dividends
                s["retained"] = retained_next

                # operating cash flow reflects TRUE economics; during fraud
                # reported earnings exceed cash generation (accrual red flag)
                operating_cf = true_ni + depreciation
                investing_cf = -capex
                financing_cf = debt_change - dividends
                net_change_cash = operating_cf + investing_cf + financing_cf

                filing_available = when + timedelta(days=60 if fq == 4 else 35)
                call_at = when + timedelta(days=30)

                quarters.append(
                    QuarterRowV2(
                        company=ticker, sector=sector, fiscal_year=fy, fiscal_quarter=fq,
                        period_end=when, available_at=filing_available,
                        revenue=revenue, cogs=cogs, gross_profit=gross_profit,
                        operating_expenses=opex, ebit=ebit, interest_expense=interest_expense,
                        pretax_income=pretax, tax_expense=tax, net_income=reported_ni,
                        eps=eps, operating_margin=operating_margin,
                        gross_margin=gross_margin_pub, fraud_flag=inflating,
                    )
                )
                balance_sheets.append(
                    BalanceSheetV2(
                        company=ticker, period_end=when, available_at=filing_available,
                        assets=assets_next, liabilities=liabilities_next, equity=equity_next,
                        cash=cash_next, receivables=receivables, inventory=inventory,
                        pp_e_net=pp_e_net, debt=debt_next, payables=payables,
                        equity_check_residual=assets_next - (liabilities_next + equity_next),
                        plug=plug,
                    )
                )
                cash_flows.append(
                    CashFlowV2(
                        company=ticker, period_end=when, available_at=filing_available,
                        operating_cf=operating_cf, investing_cf=investing_cf,
                        financing_cf=financing_cf, depreciation=depreciation,
                        capex=capex, dividends=dividends,
                        net_change_in_cash=net_change_cash,
                    )
                )
                filings.append(
                    FilingEventV2(
                        company=ticker,
                        kind="10-K" if fq == 4 else "10-Q",
                        filed_at=filing_available,
                        available_at=filing_available,
                        period_end=when,
                    )
                )

                # commit next state
                s["cash"] = cash_next
                s["debt"] = debt_next
                s["pp_e_net"] = pp_e_net
                s["receivables"] = receivables
                s["inventory"] = inventory
                s["payables"] = payables

                # ---- distress / default ------------------------------------ #
                liquidity = _clamp01(0.9 * float(s["liquidity"]) + 0.1 * (cash_next / max(assets_next, 1.0) / 0.10))
                debt_stress = _clamp01(
                    0.85 * float(s["debt_stress"])
                    + 0.15 * ((debt_next / max(equity_next, 1.0)) / 2.0
                              + max(0.0, -operating_cf) / max(revenue, 1.0) * 4.0)
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
                    gross_margin >= float(pending_guidance) * 0.98 if pending_guidance is not None else None
                )
                next_guidance = gross_margin * (1.0 + 0.02 * (float(s["mgmt_quality"]) - 0.5))
                s["guidance"] = next_guidance

                earnings.append(
                    EarningsEventV2(
                        company=ticker, call_at=call_at, period_end=when,
                        revenue=revenue, net_income=reported_ni,
                        guidance_margin=float(pending_guidance) if pending_guidance is not None else None,
                        guidance_met=guidance_met,
                    )
                )
                events.append(
                    EventV2(
                        at=call_at, company=ticker, kind="earnings_call",
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
                        at=filing_available, company=ticker,
                        kind="filing", payload={"form": "10-K" if fq == 4 else "10-Q", "period_end": when.isoformat()},
                    )
                )

                # analyst estimate issued BEFORE the print (true PIT)
                est_rng = self._est_rngs[ticker]
                period_label = f"{fy}Q{fq}"
                had_prior_est = s["prior_est"] is not None
                prior_est = s["prior_est"]
                prior_est = float(prior_est) if had_prior_est else revenue_q * (1.0 + p.analyst_bias)
                est_now = prior_est * (1.0 + p.analyst_bias) * (1.0 + est_rng.normal() * p.analyst_noise)
                issued_at = when - timedelta(days=45)
                estimates.append(
                    EstimateRowV2(
                        company=ticker, metric="revenue", period=period_label,
                        source="consensus-v0", estimate=est_now,
                        issued_at=issued_at, revised_at=call_at + timedelta(days=1),
                    )
                )
                if had_prior_est:
                    revisions.append(
                        RevisionV2(
                            company=ticker, metric="revenue", period=period_label,
                            previous_value=prior_est, revised_value=est_now,
                            original_available_at=when - timedelta(days=135),
                            revised_available_at=call_at + timedelta(days=1),
                            reason="post-earnings revision",
                        )
                    )
                s["prior_est"] = revenue  # next quarter's anchor is this actual

                # price: quarterly session at period end, earnings reaction
                price_rng = self._price_rngs[ticker]
                surprise = (reported_ni - est_now * (reported_ni / max(revenue, 1.0))) / max(
                    abs(reported_ni), 1.0
                )
                drift = 0.10 * growth / 4.0
                guidance_term = 0.0 if guidance_met is None else (0.004 if guidance_met else -0.006)
                ret = drift + p.earnings_reaction * surprise + guidance_term
                ret += price_rng.normal() * p.price_noise
                prev_close = float(s["prior_close"])
                close = max(0.5, prev_close * (1.0 + ret))
                prices.append(
                    PriceRowV2(
                        company=ticker, session=when,
                        open=prev_close, high=max(prev_close, close) * 1.005,
                        low=min(prev_close, close) * 0.995, close=close,
                        volume=max(1.0, shares * 0.001 * price_rng.uniform_range(0.5, 1.5)),
                    )
                )
                s["prior_close"] = close

                # latent snapshot (hidden)
                latents_all.setdefault(ticker, []).append(
                    LatentAtV2(
                        date=when,
                        values={
                            "demand_index": demand_index,
                            "pricing_power": float(s["pricing_power"]),
                            "market_share": float(s["market_share"]),
                            "input_cost_index": input_cost_index,
                            "labor_cost_index": labor_cost_index,
                            "debt_stress": debt_stress,
                            "liquidity": liquidity,
                            "management_quality": float(s["mgmt_quality"]),
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
        )


def run_economy(
    params: EconomyParamsV2,
    interventions: tuple[InterventionV2, ...] = (),
    world_id: str = "fuzzer-000000",
) -> WorldOutcomeV2:
    """Build and run a deterministic V2 world in one call."""
    return EconomyEngineV2(params, interventions, world_id).run()
