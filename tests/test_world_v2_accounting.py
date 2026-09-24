"""Exact M4.3 accounting contracts for the pinned FWF kernel integration."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app._vendor.fwf_kernel import ledger as L
from app._vendor.fwf_kernel.temporal import canonical_utc_instant
from app.economy.accounting_v1 import (
    CompanyAccountingV1,
    canonical_payload_hash,
    close_instant,
    decimal_text,
    exact_money,
)
from app.economy.v2 import EconomyParamsV2, run_economy
from app.export_world_v2 import export_economy_v2

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _accounting(**overrides: Any) -> CompanyAccountingV1:
    values: dict[str, Any] = {
        "ticker": "TEST",
        "initial_shares": 1_000,
        "cash_target": 10_000.00,
        "ar_target": 100.00,
        "inventory_target": 100.00,
        "ppe_target": 800.00,
        "ap_target": 50.00,
        "debt_target": 200_000.00,
        "retained_target": 2_000.00,
        "depreciation_rate": 0.05,
        "interest_rate": 0.05,
        "tax_rate": 0.21,
        "payout_ratio": 0.30,
        "min_cash_buffer_ratio": 0.10,
        "sector": "Technology",
        "horizon_quarters": 8,
        "opening_date": date(2025, 12, 31),
    }
    values.update(overrides)
    return CompanyAccountingV1(**values)


def _apply(
    accounting: CompanyAccountingV1,
    period: int,
    period_end: date,
    **overrides: Any,
) -> Any:
    values: dict[str, Any] = {
        "sector": "Technology",
        "period": period,
        "period_end": period_end,
        "true_revenue": 1_000.00,
        "gross_margin": 0.40,
        "days_receivable": 9.0,
        "days_inventory": 9.0,
        "days_payable": 9.0,
        "sga": 0.00,
        "capex": 0.00,
        "tax_rate": 0.00,
        "payout_ratio": 0.00,
        "current_interest_rate": 0.05,
        "fraud": False,
    }
    values.update(overrides)
    return accounting.apply_quarter(**values)


def _entry_amount(entries: list[Any], event: str, account: str, side: str) -> Decimal:
    return sum(
        (
            getattr(line, side)
            for entry in entries
            if entry.event == event
            for line in entry.lines
            if line.account == account
        ),
        ZERO,
    )


def _assert_no_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        pytest.fail(f"hidden accounting evidence contains a float at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_no_floats(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_floats(child, f"{path}[{index}]")


def test_vendored_kernel_is_the_exact_recorded_snapshot() -> None:
    kernel_dir = Path(__file__).parents[1] / "app" / "_vendor" / "fwf_kernel"
    source = json.loads((kernel_dir / "SOURCE.json").read_text())

    assert source["source_repository"] == "https://github.com/Scott-Switzer/financial-system-core"
    assert source["source_commit"] == "b533ef0ddf89b6de0041b2f64dc59514f40da46f"
    assert source["license"] == "MIT"
    assert len(source["source_paths"]) == 6
    assert {Path(path).name for path in source["source_paths"]} == {
        "__init__.py",
        "temporal.py",
        "ledger.py",
        "subledgers.py",
        "equity.py",
        "filings.py",
    }
    assert {path.name for path in kernel_dir.glob("*.py")} == {
        Path(path).name for path in source["source_paths"]
    }
    assert not (kernel_dir / "semantic_rng.py").exists()
    assert not (kernel_dir / "qc.py").exists()

    for source_path in source["source_paths"]:
        local_path = kernel_dir / Path(source_path).name
        digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
        assert digest == source["sha256"][source_path], source_path


def test_accounting_precision_boundary_is_decimal_str_and_cents() -> None:
    assert exact_money(0.1) == Decimal("0.10")
    assert exact_money(1.005) == Decimal("1.01")
    assert exact_money(Decimal("12.345")) == Decimal("12.35")
    with pytest.raises(TypeError, match="boolean"):
        exact_money(True)
    with pytest.raises(ValueError, match="non-negative"):
        exact_money(-CENT)
    with pytest.raises(ValueError, match="finite"):
        exact_money(float("nan"))


def test_standard_roster_has_solvent_exact_opening_state_for_64_seeds() -> None:
    for seed in range(64):
        world = run_economy(
            EconomyParamsV2(years=1, seed=seed),
            world_id=f"opening-accounting-{seed}",
            company_count=24,
        )
        assert len(world.accounting) == 24
        for company in world.companies:
            accounting = world.accounting[company["ticker"]]
            opening = L.balance_sheet(accounting.ledger, 0)
            state = accounting.opening_state

            assert opening["total_assets"] == (
                opening["cash"] + opening["ar"] + opening["inventory"] + opening["ppe_net"]
            )
            assert opening["total_assets"] == opening["total_liabilities"] + opening["total_equity"]
            assert opening["total_liabilities"] == state["ap"] + state["debt"]
            assert opening["total_equity"] == (
                state["common_stock"] + state["additional_paid_in_capital"] + state["retained_earnings"]
            )
            assert state["common_stock"] == Decimal(state["issued_shares"]) * CENT
            assert state["ap"] <= accounting.opening_targets["ap"]
            assert state["debt"] <= accounting.opening_targets["debt"]
            assert state["common_stock"] >= ZERO
            assert state["additional_paid_in_capital"] >= ZERO
            assert state["retained_earnings"] >= ZERO
            assert accounting.operational.validate_reconciliations() == set()
            assert accounting.equity.validate_reconciliations() == set()
            assert accounting.filings.validate_reconciliations() == set()


def test_every_statement_and_close_is_rederived_from_the_ledger() -> None:
    accounting = _accounting()
    period_ends = (
        date(2026, 3, 31),
        date(2026, 6, 30),
        date(2026, 9, 30),
        date(2026, 12, 31),
    )
    results = [
        _apply(
            accounting,
            period,
            period_end,
            true_revenue=1_000.00 + period * 25.00,
            capex=50.00,
            tax_rate=0.21,
            payout_ratio=0.10,
        )
        for period, period_end in enumerate(period_ends, start=1)
    ]

    for result in results:
        period = result.period
        assert result.income_statement == L.income_statement(accounting.ledger, period)
        assert result.balance_sheet == L.balance_sheet(accounting.ledger, period)
        assert result.direct_cash_flow == L.cash_flow_direct(accounting.ledger, period)
        assert result.indirect_cash_flow == L.cash_flow_indirect(accounting.ledger, period)
        assert result.direct_cash_flow == result.indirect_cash_flow
        assert result.opening_cash == accounting.ledger.balances(through=period - 1)["cash"]
        assert result.net_change_in_cash == sum(result.direct_cash_flow.values(), ZERO)
        assert result.opening_cash + result.net_change_in_cash == result.closing_cash
        assert result.balance_sheet["total_assets"] == (
            result.balance_sheet["total_liabilities"] + result.balance_sheet["total_equity"]
        )
        assert result.weighted_average_shares == Decimal(1_000)
        assert (
            accounting.equity.validate_reconciliations(
                period_start=result.period_start,
                period_end=result.period_end,
                as_of_date=result.period_end,
                net_income=result.income_statement["net_income"],
                reported_waso=result.weighted_average_shares,
                reported_eps=result.basic_eps,
            )
            == set()
        )
        assert accounting.operational.validate_reconciliations() == set()
        assert accounting.filings.validate_reconciliations() == set()

        period_entries = [entry for entry in accounting.ledger.entries if entry.period == period]
        assert period_entries[-1].event == "close"
        assert period_entries[-1].posted_at == close_instant(result.period_end)
        assert all(entry.posted_at < close_instant(result.period_end) for entry in period_entries[:-1])
        for entry in accounting.ledger.entries:
            canonical_utc_instant(entry.event_time)
            canonical_utc_instant(entry.posted_at)
            assert entry.event_time <= entry.posted_at
            assert sum((line.debit for line in entry.lines), ZERO) == sum(
                (line.credit for line in entry.lines), ZERO
            )

        assert (
            sum(
                (invoice.outstanding_amount for invoice in accounting.operational.receivables.values()),
                ZERO,
            )
            == accounting.ledger.balances()["ar"]
        )
        assert (
            sum(
                (invoice.outstanding_amount for invoice in accounting.operational.payables.values()),
                ZERO,
            )
            == accounting.ledger.balances()["ap"]
        )
        assert (
            sum(
                (layer.remaining_cost for layer in accounting.operational.inventory_layers.values()),
                ZERO,
            )
            == accounting.ledger.balances()["inventory"]
        )
        assert (
            sum((asset.gross_cost for asset in accounting.operational.ppe_assets.values()), ZERO)
            == accounting.ledger.balances()["ppe"]
        )
        assert (
            sum(
                (asset.accumulated_depreciation for asset in accounting.operational.ppe_assets.values()),
                ZERO,
            )
            == accounting.ledger.balances()["acc_dep"]
        )
        assert (
            sum(
                (tranche.principal_outstanding for tranche in accounting.operational.debt_tranches.values()),
                ZERO,
            )
            == accounting.ledger.balances()["debt"]
        )
        assert accounting.equity.issued_shares == 1_000
        assert accounting.equity.treasury_shares == 0

    assert len(accounting.filing_artifacts) == 4


def test_working_capital_changes_are_journaled_and_reconciled_exactly() -> None:
    accounting = _accounting()
    result = _apply(
        accounting,
        1,
        date(2026, 3, 31),
        days_receivable=9.0,
        days_inventory=9.0,
        days_payable=9.0,
    )

    assert result.balance_sheet["ar"] == Decimal("100.00")
    assert result.balance_sheet["inventory"] == Decimal("100.00")
    assert result.balance_sheet["ap"] == Decimal("100.00")
    opening = L.balance_sheet(accounting.ledger, 0)
    working_capital = (
        opening["ar"]
        - result.balance_sheet["ar"]
        + result.balance_sheet["inventory"]
        - opening["inventory"]
        + result.balance_sheet["ap"]
        - opening["ap"]
    )
    assert result.direct_cash_flow["operating"] == (
        result.income_statement["net_income"] + result.income_statement["depreciation"] + working_capital
    )
    interest = accounting.operational.accrued_interest[("debt:TEST:opening", 1)]
    assert interest.opening_principal == accounting.opening_state["debt"] == Decimal("10940.00")
    assert interest.accrued_amount == result.income_statement["interest"] == Decimal("136.75")
    assert interest.paid_amount == Decimal("136.75")
    assert result.balance_sheet["interest_payable"] == ZERO
    assert accounting.operational.validate_reconciliations() == set()


def test_depreciation_uses_opening_and_new_pp_e_schedules_exactly() -> None:
    accounting = _accounting(ppe_target=800.00, debt_target=0.00)
    result = _apply(
        accounting,
        1,
        date(2026, 3, 31),
        gross_margin=1.0,
        capex=400.00,
    )

    assert result.income_statement["depreciation"] == Decimal("15.00")
    assert result.balance_sheet["ppe"] == Decimal("1200.00")
    assert result.balance_sheet["acc_dep"] == Decimal("15.00")
    assert result.balance_sheet["ppe_net"] == Decimal("1185.00")
    assert result.capex == Decimal("400.00")
    assert (
        sum(
            (asset.accumulated_depreciation for asset in accounting.operational.ppe_assets.values()),
            ZERO,
        )
        == result.balance_sheet["acc_dep"]
    )


def test_liquidity_shortfall_posts_explicit_debt_and_financing_cash_flow() -> None:
    accounting = _accounting(
        cash_target=1.00,
        ar_target=1.00,
        inventory_target=2_000.00,
        ppe_target=10.00,
        ap_target=2_000.00,
        debt_target=0.00,
        retained_target=0.00,
    )
    result = _apply(
        accounting,
        1,
        date(2026, 3, 31),
        gross_margin=1.0,
        days_receivable=90.0,
        days_inventory=0.0,
        days_payable=90.0,
    )

    liquidity = accounting.operational.debt_tranches["liquidity:TEST:1:1"]
    assert liquidity.original_principal == Decimal("1098.00")
    assert result.direct_cash_flow["financing"] == Decimal("1098.00")
    assert result.balance_sheet["debt"] == Decimal("1098.00")
    assert result.closing_cash == Decimal("100.00")
    assert result.opening_cash + sum(result.direct_cash_flow.values(), ZERO) == result.closing_cash
    assert all(entry.event != "borrow" or entry.period == 1 for entry in accounting.ledger.entries)
    assert accounting.operational.validate_reconciliations() == set()


def test_fraud_creates_an_uncollected_invoice_and_exact_reported_revenue() -> None:
    honest = _apply(_accounting(), 1, date(2026, 3, 31), fraud=False)
    accounting = _accounting()
    flagged = _apply(
        accounting,
        1,
        date(2026, 3, 31),
        days_receivable=9.0,
        fraud=True,
    )

    fraud_invoice = accounting.operational.receivables["fraud:TEST:1"]
    genuine_invoice = accounting.operational.receivables["ar:TEST:1:genuine"]
    assert fraud_invoice.original_amount == Decimal("4.00")
    assert fraud_invoice.outstanding_amount == Decimal("4.00")
    assert genuine_invoice.outstanding_amount == Decimal("100.00")
    assert flagged.balance_sheet["ar"] == Decimal("104.00")
    assert flagged.income_statement["revenue"] == Decimal("1004.00")
    assert honest.income_statement["revenue"] == Decimal("1000.00")
    assert _entry_amount(
        [entry for entry in accounting.ledger.entries if entry.period == 1],
        "sale_on_credit",
        "revenue",
        "credit",
    ) == Decimal("1004.00")
    assert accounting.operational.validate_reconciliations() == set()


def test_tax_dividends_and_close_reconcile_to_retained_earnings() -> None:
    accounting = _accounting(debt_target=0.00)
    result = _apply(
        accounting,
        1,
        date(2026, 3, 31),
        gross_margin=0.60,
        tax_rate=0.25,
        payout_ratio=0.50,
    )
    entries = [entry for entry in accounting.ledger.entries if entry.period == 1]

    assert result.income_statement["tax_expense"] == Decimal("147.50")
    assert result.balance_sheet["tax_payable"] == ZERO
    assert result.dividends == Decimal("221.25")
    assert _entry_amount(entries, "accrue_tax", "tax_expense", "debit") == Decimal("147.50")
    assert _entry_amount(entries, "pay_tax", "tax_payable", "debit") == Decimal("147.50")
    assert _entry_amount(entries, "pay_dividend", "dividends", "debit") == Decimal("221.25")
    assert result.balance_sheet["retained_earnings"] == (
        accounting.opening_state["retained_earnings"]
        + result.income_statement["net_income"]
        - result.dividends
    )
    assert result.balance_sheet["total_assets"] == (
        result.balance_sheet["total_liabilities"] + result.balance_sheet["total_equity"]
    )


def test_filing_commitments_hidden_evidence_and_public_sealing(tmp_path: Path) -> None:
    world = run_economy(
        EconomyParamsV2(years=2, seed=17),
        world_id="accounting-export-world",
        company_count=1,
    )
    output = export_economy_v2(world, tmp_path / "release")

    public_filings = json.loads((output / "public" / "filings.json").read_text())["filings"]
    exported_by_id = {row["filing_id"]: row for row in public_filings}
    for filing in world.filings:
        row = exported_by_id[filing.filing_id]
        assert row["version_id"] == filing.version_id
        assert row["version"] == filing.version == 1
        assert row["payload_sha256"] == filing.payload_sha256
        company_accounting = world.accounting[filing.company]
        artifact = next(
            candidate
            for candidate in company_accounting.filing_artifacts
            if candidate.filing_id == filing.filing_id
        )
        result = next(
            candidate
            for candidate in company_accounting.period_results
            if candidate.filing.filing_id == filing.filing_id
        )
        assert canonical_payload_hash(artifact.payload) == filing.payload_sha256
        assert artifact.payload["income_statement"] == {
            key: decimal_text(value) for key, value in result.income_statement.items()
        }
        assert artifact.payload["balance_sheet"] == {
            key: decimal_text(value) for key, value in result.balance_sheet.items()
        }
        assert artifact.payload["cash_flow"] == {
            key: decimal_text(value) for key, value in result.direct_cash_flow.items()
        }

    hidden_accounting = json.loads((output / "hidden" / "accounting.json").read_text())
    _assert_no_floats(hidden_accounting)
    assert hidden_accounting["precision"] == "exact decimal strings"
    for ticker, evidence in hidden_accounting["companies"].items():
        assert evidence["reconciliations"] == {
            "operational_failures": [],
            "equity_failures": [],
            "filing_failures": [],
        }
        assert all(period["checks"]["balance_identity_exact"] for period in evidence["periods"])
        assert all(period["checks"]["direct_indirect_exact"] for period in evidence["periods"])
        assert all(period["checks"]["cash_roll_exact"] for period in evidence["periods"])
        assert evidence["ticker"] == ticker

    manifest = json.loads((output / "manifest.json").read_text())
    assert "hidden/accounting.json" in manifest["hidden_artifacts"]
    for relative, metadata in manifest["artifact_hashes"].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == metadata["sha256"]

    observations = json.loads((output / "public" / "financials.json").read_text())["observations"]
    metrics = {row["metric"] for row in observations}
    assert {
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "weighted_average_shares",
        "eps",
        "assets",
        "liabilities",
        "equity",
        "operating_cash_flow",
        "investing_cash_flow",
        "financing_cash_flow",
        "net_change_in_cash",
    } <= metrics

    public_paths = sorted((output / "public").glob("*.json")) + [output / "manifest.json"]
    public_text = "\n".join(path.read_text() for path in public_paths)
    for forbidden in (
        "fraud",
        "fraud_flag",
        "latents",
        "stream_registry",
        "rng_world_id",
        "journal_entries",
        "opening_targets",
        "receivable_invoices",
    ):
        assert forbidden not in public_text
    for internal_detail in ("ROSTER:", "COMPANY:", "SECTOR:", world.rng_world_id):
        assert internal_detail not in public_text
