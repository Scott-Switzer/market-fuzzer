"""MechanismSpec declaration of MARKET_FUZZER_WORLD_V2 (``app.economy.v2``).

The engine is still one loop. This module is the machine-readable statement of
which mechanism reads and writes which variable, kept honest by
``tests/m6``: an RNG-address completeness check against a real run, a source
scan of every ``_clamp`` call, a row-field completeness check against the
output dataclasses, and the twin-world invariance test.

Granularity is per mechanism. All outputs of one mechanism are treated as
depending on all of its inputs, so the descendant sets are conservative
supersets. The accounting kernel is one mechanism (``company.accounting``)
because it is one ledger-authoritative call.

Time: ``@t-1`` marks a lagged read. ``active@t-1`` gates every company
mechanism because a defaulted company stops producing rows.
"""

from __future__ import annotations

from app.causal.registry import MechanismRegistry, MechanismSpec

ENTITY_WORLD = "WORLD"

QUARTER_FIELDS = (
    "available_at",
    "revenue",
    "cogs",
    "gross_profit",
    "operating_expenses",
    "ebit",
    "interest_expense",
    "pretax_income",
    "tax_expense",
    "net_income",
    "weighted_average_shares",
    "eps",
    "operating_margin",
    "gross_margin",
)
BALANCE_FIELDS = (
    "available_at",
    "assets",
    "liabilities",
    "equity",
    "cash",
    "receivables",
    "inventory",
    "pp_e_net",
    "debt",
    "payables",
)
CASH_FLOW_FIELDS = (
    "available_at",
    "operating_cf",
    "investing_cf",
    "financing_cf",
    "depreciation",
    "capex",
    "dividends",
    "net_change_in_cash",
)
FILING_FIELDS = (
    "kind",
    "filed_at",
    "available_at",
    "filing_id",
    "version_id",
    "version",
    "payload_sha256",
)
EARNINGS_FIELDS = ("call_at", "revenue", "net_income", "guidance_margin", "guidance_met")
ESTIMATE_FIELDS = ("source", "estimate", "issued_at", "revised_at")
REVISION_FIELDS = (
    "previous_value",
    "revised_value",
    "original_available_at",
    "revised_available_at",
    "reason",
)
PRICE_FIELDS = ("open", "high", "low", "close", "volume")
MACRO_REAL_FIELDS = ("gdp_growth", "inflation", "regime")
MACRO_RATE_FIELDS = ("policy_rate", "credit_index")

# latent snapshot key -> the variable it snapshots
LATENT_SOURCES: dict[str, str] = {
    "demand_index": "demand_index",
    "pricing_power": "pricing_power",
    "market_share": "market_share",
    "input_cost_index": "input_cost_index",
    "labor_cost_index": "labor_cost_index",
    "debt_stress": "debt_stress",
    "liquidity": "liquidity",
    "management_quality": "management_quality",
    "fraud_propensity": "fraud_propensity",
    "competitive_intensity": "industry.intensity",
}

# Variables the engine clamps through ``EconomyEngineV2._clamp``.
CLAMPABLE = ("demand_index", "fraud_propensity", "rate_shock_bps", "supplier_failure")

ACTIVE = "active@t-1"


def _fields(table: str, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{table}.{name}" for name in names)


def build_world_v2_registry() -> MechanismRegistry:
    reg = MechanismRegistry()
    reg.declare_exogenous("rate_shock_bps", "world", intervenable=True)
    reg.declare_exogenous("supplier_failure", "company", intervenable=True)

    def add(
        mid: str,
        scope: str,
        inputs: tuple[str, ...],
        outputs: tuple[str, ...],
        rng: tuple[str, ...] = (),
        *,
        tier: str = "public",
        gated: bool = True,
        doc: str = "",
    ) -> None:
        if scope == "company" and gated:
            inputs = (*inputs, ACTIVE)
        reg.register(
            MechanismSpec(
                id=mid,
                version=1,
                scope=scope,  # type: ignore[arg-type]
                inputs=inputs,
                outputs=outputs,
                rng_addresses=rng,
                tier_of_outputs=tier,  # type: ignore[arg-type]
                doc=doc,
            )
        )

    # ---- structural roster (fixed before any intervention) ------------------ #
    add(
        "world.roster",
        "world",
        (),
        ("world.roster",),
        tuple(
            f"ROSTER/roster/{v}/uniform01@T1" for v in ("name_root", "name_suffix", "sector", "ticker_letter")
        ),
        tier="hidden",
        doc="Company names, tickers and sectors. Structural: interventions never alter the roster.",
    )

    # ---- world ------------------------------------------------------------- #
    add(
        "world.macro_real",
        "world",
        ("macro.regime@t-1",),
        _fields("macro", MACRO_REAL_FIELDS),
        (
            "WORLD/macro/regime_transition/uniform01@T1",
            "WORLD/macro/regime_transition_probability/uniform01@T1",
        ),
        doc="GDP cycle, inflation cycle and Markov regime.",
    )
    add(
        "world.macro_rates",
        "world",
        ("rate_shock_bps", "macro.gdp_growth", "macro.inflation", "macro.regime"),
        _fields("macro", MACRO_RATE_FIELDS),
        doc="Policy rate (with do-clamped rate shock) and credit index.",
    )

    # ---- sector ------------------------------------------------------------ #
    add(
        "sector.industry",
        "sector",
        ("industry.momentum@t-1", "industry.shock@t-1", "industry.intensity@t-1"),
        ("industry.momentum", "industry.shock", "industry.intensity"),
        (
            "SECTOR/industry/competitive_intensity/normal@T1",
            "SECTOR/industry/momentum_shock/normal@T1",
            "SECTOR/industry/shock_event/uniform01@T1",
            "SECTOR/industry/shock_magnitude/normal@T1",
        ),
        tier="hidden",
        doc="Sector momentum, shock and competitive intensity.",
    )

    # ---- company: static initial state ------------------------------------- #
    add(
        "company.initial_state",
        "company",
        (),
        (
            "initial.balance_sheet",
            "initial.growth",
            "initial.growth0",
            "initial.gross_margin",
            "initial.gross_margin0",
            "initial.liquidity",
            "pricing_power",
            "market_share",
            "management_quality",
        ),
        tuple(
            f"COMPANY/initial_state/{v}/{d}@T1"
            for v, d in (
                ("equity_fraction", "uniform01"),
                ("gross_margin", "normal"),
                ("gross_margin0", "normal"),
                ("growth", "normal"),
                ("growth0", "normal"),
                ("initial_shares", "uniform01"),
                ("leverage", "normal"),
                ("liquidity", "normal"),
                ("management_quality", "normal"),
                ("market_share", "normal"),
                ("pricing_power", "normal"),
                ("size", "normal"),
            )
        ),
        tier="hidden",
        gated=False,
        doc="Opening balance sheet, share count and latent traits. Time-invariant.",
    )

    # ---- company: operations ----------------------------------------------- #
    add(
        "company.demand",
        "company",
        ("demand_index@t-1", "macro.gdp_growth", "industry.momentum", "industry.shock"),
        ("demand_index",),
        ("COMPANY/operations/demand_noise/normal@T1",),
        tier="hidden",
        doc="Demand index; clampable by do(demand_index).",
    )
    add(
        "company.cost_indices",
        "company",
        ("input_cost_index@t-1", "labor_cost_index@t-1", "macro.inflation"),
        ("input_cost_index", "labor_cost_index"),
        (
            "COMPANY/operations/input_cost_noise/normal@T1",
            "COMPANY/operations/labor_cost_noise/normal@T1",
        ),
        tier="hidden",
    )
    add(
        "company.growth",
        "company",
        (
            "growth@t-1",
            "initial.growth",
            "initial.growth0",
            "management_quality",
            "macro.gdp_growth",
            "industry.intensity",
        ),
        ("growth",),
        ("COMPANY/operations/growth_noise/normal@T1",),
        tier="hidden",
    )
    add(
        "company.revenue",
        "company",
        ("revenue_q@t-1", "growth", "demand_index", "demand_index@t-1", "initial.balance_sheet"),
        ("revenue_q",),
        tier="hidden",
        doc="True quarterly revenue before any fraud inflation.",
    )
    add(
        "company.margin",
        "company",
        (
            "initial.gross_margin0",
            "input_cost_index",
            "labor_cost_index",
            "pricing_power",
            "industry.intensity",
            "management_quality",
            "supplier_failure",
        ),
        ("margin.gross_true",),
        ("COMPANY/operations/gross_margin_noise/normal@T1",),
        tier="hidden",
    )
    add(
        "company.fraud_propensity",
        "company",
        (),
        ("fraud_propensity",),
        ("COMPANY/operations/fraud_propensity/normal@T1",),
        tier="hidden",
        doc="Always drawn, then clamped: interventions never shift the draw sequence.",
    )
    add(
        "company.fraud_state",
        "company",
        ("fraud_propensity", "debt_stress@t-1", "liquidity@t-1", "initial.liquidity", "fraud_open@t-1"),
        ("fraud_open",),
        tier="hidden",
    )
    add(
        "company.inflating",
        "company",
        ("fraud_open", "supplier_failure"),
        ("quarters.fraud_flag",),
        doc="Fraud inflation is suppressed while a supplier failure is active.",
    )

    # ---- company: ledger-authoritative accounting -------------------------- #
    add(
        "company.accounting",
        "company",
        (
            "revenue_q",
            "margin.gross_true",
            "labor_cost_index",
            "quarters.fraud_flag",
            "macro.policy_rate",
            "macro.credit_index",
            "initial.balance_sheet",
            "accounting.state@t-1",
        ),
        (
            *_fields("quarters", QUARTER_FIELDS),
            *_fields("balance_sheets", BALANCE_FIELDS),
            *_fields("cash_flows", CASH_FLOW_FIELDS),
            *_fields("filings", FILING_FIELDS),
            "accounting.state",
        ),
        doc="One call into the FWF kernel; statements derive from the journal.",
    )

    # ---- company: distress and default ------------------------------------- #
    add(
        "company.distress",
        "company",
        (
            "liquidity@t-1",
            "debt_stress@t-1",
            "initial.liquidity",
            "balance_sheets.cash",
            "balance_sheets.assets",
            "balance_sheets.debt",
            "balance_sheets.equity",
            "cash_flows.operating_cf",
            "quarters.revenue",
        ),
        ("liquidity", "debt_stress", "active", "defaults"),
        tier="hidden",
        doc="A default stops row production for the company from that quarter on.",
    )
    add(
        "company.fraud_windows",
        "company",
        ("fraud_open", "active"),
        ("fraud_windows",),
        tier="hidden",
    )

    # ---- company: post-default publications -------------------------------- #
    add(
        "company.earnings",
        "company",
        (
            "quarters.gross_margin",
            "quarters.revenue",
            "quarters.net_income",
            "management_quality",
            "guidance@t-1",
            "active",
        ),
        (*_fields("earnings", EARNINGS_FIELDS), "guidance"),
    )
    add(
        "company.earnings_call_event",
        "company",
        (
            "quarters.gross_margin",
            "quarters.revenue",
            "quarters.net_income",
            "earnings.guidance_met",
            "earnings.call_at",
            "active",
        ),
        ("events.earnings_call",),
    )
    add(
        "company.filing_event",
        "company",
        ("filings.kind", "filings.available_at", "active"),
        ("events.filing",),
    )
    add(
        "company.estimates",
        "company",
        ("quarters.revenue@t-1", "revenue_q", "active"),
        _fields("estimates", ESTIMATE_FIELDS),
        ("COMPANY/estimates/revenue_error/normal@T1",),
    )
    add(
        "company.revisions",
        "company",
        ("quarters.revenue@t-1", "estimates.estimate", "active"),
        _fields("revisions", REVISION_FIELDS),
    )
    add(
        "company.price",
        "company",
        ("growth", "prices.close@t-1", "initial.balance_sheet", "active"),
        _fields("prices", PRICE_FIELDS),
        (
            "COMPANY/price/return_noise/normal@T1",
            "COMPANY/price/volume_scale/uniform01@T1",
        ),
        doc="Quarter-end price excludes current-quarter earnings and guidance (no look-ahead).",
    )
    for key, source in LATENT_SOURCES.items():
        add(
            f"company.latent.{key}",
            "company",
            (source, "active"),
            (f"latents.{key}",),
            tier="hidden",
            gated=False,
        )

    reg.allow_intervention("demand_index", "fraud_propensity")
    return reg.validate()


__all__ = [
    "CLAMPABLE",
    "LATENT_SOURCES",
    "build_world_v2_registry",
]
