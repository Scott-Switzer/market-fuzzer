"""QC_V2 accounting core: rule registry, independent re-derivation, rule-targeted mutations.

Principles:
  * Every rule has an id, an evidence class (PROVEN = exact identity; MEASURED = statistical;
    ASSUMED = declared, untested), and a severity.
  * QC never trusts published derived fields; it recomputes from the journal.
  * Every mutation declares the rule ids it MUST trip. A mutation that trips nothing, trips
    only unexpected rules, or is a no-op fails the suite. Coverage is written to
    qc_rule_coverage.json so uncovered rules are visible.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from .ledger import Ledger, balance_sheet, cash_flow_direct, cash_flow_indirect, income_statement
from .temporal import canonical_utc_instant


@dataclass(frozen=True)
class Rule:
    rule_id: str
    evidence: str  # PROVEN | MEASURED | ASSUMED
    severity: str  # critical | major | minor
    description: str


RULES: dict[str, Rule] = {
    r.rule_id: r
    for r in [
        Rule(
            "ACCT-IS-RECON", "PROVEN", "critical", "Published IS equals IS re-derived from journal"
        ),
        Rule(
            "ACCT-BS-RECON", "PROVEN", "critical", "Published BS equals BS re-derived from journal"
        ),
        Rule("ACCT-BALANCE", "PROVEN", "critical", "Assets = Liabilities + Equity on published BS"),
        Rule(
            "ACCT-CF-DIRECT-VS-INDIRECT",
            "PROVEN",
            "critical",
            "Direct CF equals indirect CF by section",
        ),
        Rule("ACCT-CASH-ROLL", "PROVEN", "critical", "Opening cash + net CF = closing cash"),
        Rule(
            "PIT-PUBLICATION-TIME",
            "PROVEN",
            "critical",
            "Artifact available_at exactly matches an independent canonical publication time",
        ),
    ]
}


def publish(
    led: Ledger,
    periods: list[int],
    available_at_by_period: Mapping[int, str],
) -> dict[int, dict]:
    """Publish statements with availability from an independent mechanism."""
    rep = {}
    for p in periods:
        if p not in available_at_by_period:
            raise ValueError(f"missing independent publication time for period {p}")
        available_at = available_at_by_period[p]
        canonical_utc_instant(available_at)
        rep[p] = {
            "is": income_statement(led, p),
            "bs": balance_sheet(led, p),
            "cf": cash_flow_direct(led, p),
            "available_at": available_at,
        }
    return rep


def qc(
    led: Ledger,
    published: dict[int, dict],
    periods: list[int],
    available_at_by_period: Mapping[int, str],
) -> set[str]:
    """Return the set of fired rule ids (formatted RULE:period)."""
    fired: set[str] = set()
    for p in periods:
        pub = published[p]
        if pub["is"] != income_statement(led, p):
            fired.add(f"ACCT-IS-RECON:{p}")
        if pub["bs"] != balance_sheet(led, p):
            fired.add(f"ACCT-BS-RECON:{p}")
        bs = pub["bs"]
        if bs["total_assets"] != bs["total_liabilities"] + bs["total_equity"]:
            fired.add(f"ACCT-BALANCE:{p}")
        if pub["cf"] != cash_flow_indirect(led, p):
            fired.add(f"ACCT-CF-DIRECT-VS-INDIRECT:{p}")
        prev_cash = (
            published[p - 1]["bs"]["cash"]
            if (p - 1) in published
            else balance_sheet(led, p - 1)["cash"]
        )
        if prev_cash + sum(pub["cf"].values()) != bs["cash"]:
            fired.add(f"ACCT-CASH-ROLL:{p}")
        expected = available_at_by_period.get(p)
        if expected is None:
            fired.add(f"PIT-PUBLICATION-TIME:{p}")
        else:
            try:
                canonical_utc_instant(expected)
            except (TypeError, ValueError):
                fired.add(f"PIT-PUBLICATION-TIME:{p}")
            else:
                if pub.get("available_at") != expected:
                    fired.add(f"PIT-PUBLICATION-TIME:{p}")
    return fired


@dataclass(frozen=True)
class Mutation:
    mutation_id: str
    target_rule_ids: frozenset[str]
    apply: Callable[[dict, int, Decimal], None]


def _bump(section: str, key: str) -> Callable[[dict, int, Decimal], None]:
    def f(r: dict, p: int, eps: Decimal) -> None:
        r[p][section][key] = r[p][section][key] + eps

    return f


def _reclass(r: dict, p: int, eps: Decimal) -> None:
    r[p]["bs"]["ar"] += eps
    r[p]["bs"]["inventory"] -= eps


def _early_available(r: dict, p: int, eps: Decimal) -> None:
    r[p]["available_at"] = "2025-01-01T00:00:00Z"


MUTATIONS: list[Mutation] = [
    Mutation(
        "ACCT_BREAK_BALANCE",
        frozenset({"ACCT-BALANCE", "ACCT-BS-RECON"}),
        _bump("bs", "total_assets"),
    ),
    Mutation(
        "ACCT_CF_DRIFT",
        frozenset({"ACCT-CF-DIRECT-VS-INDIRECT", "ACCT-CASH-ROLL"}),
        _bump("cf", "operating"),
    ),
    Mutation("ACCT_IS_DRIFT", frozenset({"ACCT-IS-RECON"}), _bump("is", "revenue")),
    Mutation("ACCT_BS_RECLASS", frozenset({"ACCT-BS-RECON"}), _reclass),
    Mutation("PIT_EARLY_RELEASE", frozenset({"PIT-PUBLICATION-TIME"}), _early_available),
]

SEVERITY_SWEEP = (Decimal("0.01"), Decimal(1), Decimal(1000000))


def detection_matrix(
    led: Ledger,
    periods: list[int],
    available_at_by_period: Mapping[int, str],
) -> dict:
    """Run every mutation x period x severity. Raises on false positives, no-ops, or misses."""
    base = publish(led, periods, available_at_by_period)
    fp = qc(led, base, periods, available_at_by_period)
    if fp:
        raise AssertionError(f"false positives on clean world: {sorted(fp)}")
    covered: set[str] = set()
    rows = []
    for m in MUTATIONS:
        for p in periods:
            for eps in SEVERITY_SWEEP:
                r = copy.deepcopy(base)
                m.apply(r, p, eps)
                if r == base:
                    raise AssertionError(f"{m.mutation_id} no-op at period {p}")
                fired = {f.split(":")[0] for f in qc(led, r, periods, available_at_by_period)}
                missing = m.target_rule_ids - fired
                if missing:
                    raise AssertionError(
                        f"{m.mutation_id}@{p}/{eps} did not fire {sorted(missing)}"
                    )
                covered |= fired
                rows.append(
                    {
                        "mutation": m.mutation_id,
                        "period": p,
                        "severity": str(eps),
                        "fired": sorted(fired),
                    }
                )
    return {
        "rules": sorted(RULES),
        "covered": sorted(covered),
        "uncovered": sorted(set(RULES) - covered),
        "trials": rows,
    }
