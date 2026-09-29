"""Normalized account chart shared by both sides of the differential oracle.

The FWF canonical chart is the reference taxonomy. This module restates it as
plain data so that the *comparison* does not depend on either implementation:

* :data:`FWF_ACCOUNTS` is the exact set of accounts the oracle compares.
* :data:`DEBIT_POSITIVE_SIGN` converts any implementation's balance into one
  convention -- debit positive -- so a liability with a 500.00 credit balance
  is ``-500.00`` on both sides. This is a taxonomy mapping, not accounting
  logic, and it is defined here rather than imported from either side.
* :data:`ORACLE_ACCOUNT_TYPE` names the ``python-accounting`` account type that
  receives each FWF account. The library's taxonomy has no additional-paid-in
  capital, treasury stock, or dividends accounts, so those map onto the
  library's EQUITY and RECONCILIATION types; the balance comparison is
  unaffected because the trial balance is compared per account id.

The sign table is stated explicitly rather than derived from
``fwf_kernel.ledger.CHART`` so that a wrong normal balance in either
implementation cannot silently align the two sides.
"""

from __future__ import annotations

from decimal import Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

#: The exact normalized chart compared by the oracle, in canonical order.
FWF_ACCOUNTS: tuple[str, ...] = (
    "cash",
    "ar",
    "inventory",
    "ppe",
    "acc_dep",
    "ap",
    "tax_payable",
    "interest_payable",
    "debt",
    "common_stock",
    "additional_paid_in_capital",
    "treasury_stock",
    "retained_earnings",
    "dividends",
    "revenue",
    "cogs",
    "sga",
    "depreciation",
    "interest",
    "tax_expense",
)

#: +1 when the account's natural balance is a debit, -1 when it is a credit.
DEBIT_POSITIVE_SIGN: dict[str, int] = {
    "cash": +1,
    "ar": +1,
    "inventory": +1,
    "ppe": +1,
    "acc_dep": -1,
    "ap": -1,
    "tax_payable": -1,
    "interest_payable": -1,
    "debt": -1,
    "common_stock": -1,
    "additional_paid_in_capital": -1,
    "treasury_stock": +1,
    "retained_earnings": -1,
    "dividends": +1,
    "revenue": -1,
    "cogs": +1,
    "sga": +1,
    "depreciation": +1,
    "interest": +1,
    "tax_expense": +1,
}

#: FWF account -> ``python_accounting.models.Account.AccountType`` member name.
ORACLE_ACCOUNT_TYPE: dict[str, str] = {
    "cash": "BANK",
    "ar": "RECEIVABLE",
    "inventory": "INVENTORY",
    "ppe": "NON_CURRENT_ASSET",
    "acc_dep": "CONTRA_ASSET",
    "ap": "PAYABLE",
    "tax_payable": "CURRENT_LIABILITY",
    "interest_payable": "CURRENT_LIABILITY",
    "debt": "NON_CURRENT_LIABILITY",
    "common_stock": "EQUITY",
    "additional_paid_in_capital": "EQUITY",
    "treasury_stock": "RECONCILIATION",
    "retained_earnings": "EQUITY",
    "dividends": "RECONCILIATION",
    "revenue": "OPERATING_REVENUE",
    "cogs": "DIRECT_EXPENSE",
    "sga": "OPERATING_EXPENSE",
    "depreciation": "OVERHEAD_EXPENSE",
    "interest": "DIRECT_EXPENSE",
    "tax_expense": "OTHER_EXPENSE",
}

#: Accounts that a period close moves into retained earnings.
P_AND_L_ACCOUNTS: tuple[str, ...] = (
    "revenue",
    "cogs",
    "sga",
    "depreciation",
    "interest",
    "tax_expense",
    "dividends",
)


def empty_balances() -> dict[str, Decimal]:
    """Return a zero trial balance over the full normalized chart."""
    return dict.fromkeys(FWF_ACCOUNTS, ZERO)


def to_debit_positive(account: str, normal_direction_balance: Decimal) -> Decimal:
    """Convert a normal-direction balance into the debit-positive convention."""
    return normal_direction_balance * DEBIT_POSITIVE_SIGN[account]
