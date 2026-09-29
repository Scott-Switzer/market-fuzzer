import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from machine_settings import machine_settings

from fwf_kernel import ledger as L
from fwf_kernel import qc as Q
from fwf_kernel.semantic_rng import (
    StreamAddress,
    StreamCollisionError,
    StreamRegistry,
    normal,
    raw_uint64,
    uniform01,
)

GOLDEN = Path(__file__).parent / "golden" / "rng_v3.json"
A = StreamAddress("W1", "ENT-001", "demand", "unit_sales", "uniform01@T1")
N = StreamAddress("W1", "ENT-001", "demand", "shock", "normal@T1")


# ---------------- RNG ----------------
def test_rng_golden_vectors_bit_exact():
    g = json.loads(GOLDEN.read_text())
    assert [int(x) for x in raw_uint64(A, 3, 0, 4)] == g["raw_A_p3_d0_n4"]
    assert uniform01(A, 3, 0, 4).tolist() == g["uniform_A_p3_d0_n4"]
    np.testing.assert_array_max_ulp(normal(N, 0, 0, 3), np.array(g["normal_N_p0_d0_n3"]), maxulp=4)


def test_rng_multi_block_addresses_do_not_overlap():
    period_3 = raw_uint64(A, 3, 0, 8)
    period_4 = raw_uint64(A, 4, 0, 8)
    draw_1 = raw_uint64(A, 3, 1, 8)
    assert set(period_3).isdisjoint(period_4)
    assert set(period_3).isdisjoint(draw_1)


def test_rng_same_address_is_deterministic_and_order_independent():
    before = uniform01(A, 5, 7, 3)
    for eid in ("ENT-002", "ENT-999"):  # "adding entities" and drawing other streams first
        uniform01(StreamAddress("W1", eid, "demand", "unit_sales", "uniform01@T1"), 5, 7, 3)
    assert uniform01(A, 5, 7, 3).tolist() == before.tolist()


def test_rng_distinct_addresses_distinct_streams():
    other = StreamAddress("W1", "ENT-001", "demand", "unit_sales", "uniform01@T2")
    assert raw_uint64(A, 0, 0, 2).tolist() != raw_uint64(other, 0, 0, 2).tolist()
    assert raw_uint64(A, 0, 0, 2).tolist() != raw_uint64(A, 1, 0, 2).tolist()


def test_rng_owner_collision_detected():
    reg = StreamRegistry()
    reg.claim(A, "mech.demand")
    reg.claim(A, "mech.demand")  # idempotent for the same owner
    with pytest.raises(StreamCollisionError):
        reg.claim(A, "mech.pricing")


def test_rng_distribution_guard():
    with pytest.raises(ValueError):
        normal(A, 0, 0)


# ---------------- ledger ----------------
TS = "2025-01-01T00:00:00Z"
PUBLICATION_TIMES = {
    1: "2025-01-28T00:00:00Z",
    2: "2025-02-28T00:00:00Z",
    3: "2025-03-28T00:00:00Z",
}


def ts(p: int, day: int = 1) -> tuple[str, str]:
    return (
        f"2025-{p:02d}-{day:02d}T00:00:00Z",
        f"2025-{p:02d}-{day:02d}T01:00:00Z",
    )


def sample_ledger() -> L.Ledger:
    led = L.Ledger("ENT-001")
    led.post(
        L.make(
            "open",
            "ENT-001",
            0,
            "opening",
            {"cash": "100", "ppe": "50"},
            {"common_stock": "120", "retained_earnings": "30"},
            TS,
            TS,
        )
    )
    led.close(0, TS)
    n = 0
    for p in (1, 2, 3):
        for name, amt in [
            ("sale_on_credit", "90"),
            ("collect_ar", "70"),
            ("buy_inventory_on_credit", "40"),
            ("ship_goods", "35"),
            ("pay_ap", "30"),
            ("pay_sga", "15"),
            ("capex", "10"),
            ("depreciate", "5"),
            ("borrow", "20"),
            ("pay_interest", "2"),
            ("accrue_tax", "6"),
            ("pay_tax", "4"),
            ("pay_dividend", "3"),
            ("repay", "5"),
            ("issue_equity", "1"),
        ]:
            n += 1
            led.post(L.event(f"e{n}", "ENT-001", p, name, amt, *ts(p)))
        led.close(p, f"2025-{p:02d}-28T00:00:00Z")
    return led


def test_journal_entry_rejects_negative_period_before_insertion():
    with pytest.raises(ValueError, match="period must be >= 0"):
        L.event("negative", "ENT-001", -1, "pay_sga", "1", TS, TS)


def test_empty_ledger_rejects_negative_period_entry_and_close():
    entry = object.__new__(L.JournalEntry)
    for name, value in {
        "entry_id": "negative",
        "entity_id": "ENT-001",
        "period": -1,
        "event": "pay_sga",
        "lines": (L.Line("sga", debit=Decimal(1)), L.Line("cash", credit=Decimal(1))),
        "event_time": TS,
        "posted_at": TS,
    }.items():
        object.__setattr__(entry, name, value)

    led = L.Ledger("ENT-001")
    with pytest.raises(ValueError, match="period must be >= 0"):
        led.post(entry)
    with pytest.raises(ValueError, match="period must be >= 0"):
        led.close(-1, TS)


def test_period_zero_opening_behavior_is_unchanged():
    led = L.Ledger("ENT-001")
    opening = L.make(
        "open",
        "ENT-001",
        0,
        "opening",
        {"cash": "100"},
        {"common_stock": "100"},
        TS,
        TS,
    )
    led.post(opening)
    led.close(0, TS)
    assert led.entries == [opening]
    assert led.closed_periods == {0}
    assert led.balances(only=0)["cash"] == Decimal(100)


def test_statements_identities():
    led = sample_ledger()
    for p in (1, 2, 3):
        bs = L.balance_sheet(led, p)
        assert bs["total_assets"] == bs["total_liabilities"] + bs["total_equity"]
        assert L.cash_flow_direct(led, p) == L.cash_flow_indirect(led, p)
    assert L.income_statement(led, 1)["net_income"] == Decimal(27)


def test_timestamps_and_period_rules_enforced():
    led = sample_ledger()
    with pytest.raises(ValueError):  # posted before event
        L.event(
            "x",
            "ENT-001",
            4,
            "pay_sga",
            "1",
            "2025-04-02T00:00:00Z",
            "2025-04-01T00:00:00Z",
        )
    for invalid in (
        "2025-04-02T00:00:00",  # naive
        "2025-04-02T00:00:00+00:00",  # noncanonical offset
        "2025-04-02T00:00:00.000Z",  # noncanonical precision
        "2025-02-30T00:00:00Z",  # malformed calendar instant
    ):
        with pytest.raises(ValueError):
            L.event("bad-time", "ENT-001", 4, "pay_sga", "1", invalid, TS)
    with pytest.raises(ValueError):  # closed period
        led.post(L.event("y", "ENT-001", 2, "pay_sga", "1", *ts(2)))
    with pytest.raises(ValueError):  # unbalanced / empty
        L.make("z", "ENT-001", 4, "adj", {"cash": "1"}, {"ar": "2"}, *ts(4))


def test_publication_time_comes_from_independent_schedule():
    led = sample_ledger()
    with pytest.raises(ValueError, match="missing independent publication time"):
        Q.publish(led, [1], {})
    published = Q.publish(led, [1], PUBLICATION_TIMES)
    assert published[1]["available_at"] == PUBLICATION_TIMES[1]
    assert Q.qc(led, published, [1], PUBLICATION_TIMES) == set()


def test_mutation_matrix_every_mutation_fires_its_rules(tmp_path):
    led = sample_ledger()
    cov = Q.detection_matrix(led, [1, 2, 3], PUBLICATION_TIMES)
    assert cov["uncovered"] == []
    (tmp_path / "qc_rule_coverage.json").write_text(json.dumps(cov, indent=1))


# -------- stateful property test: only valid operations, invariants after every step --------
class LedgerMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.led = L.Ledger("ENT-P")
        self.led.post(
            L.make(
                "open",
                "ENT-P",
                0,
                "opening",
                {"cash": "1000"},
                {"common_stock": "1000"},
                TS,
                TS,
            )
        )
        self.led.close(0, TS)
        self.p, self.n = 1, 0

    def _bal(self, a):
        return self.led.balances(through=self.p)[a]

    def _post(self, name, amt):
        self.n += 1
        self.led.post(L.event(f"s{self.n}", "ENT-P", self.p, name, amt, *ts(min(self.p, 12))))

    amounts = st.integers(1, 500).map(Decimal)

    @rule(a=amounts)
    def sell(self, a):
        self._post("sale_on_credit", a)

    @precondition(lambda s: s._bal("ar") > 0)
    @rule(f=st.fractions(0, 1))
    def collect(self, f):
        amt = (self._bal("ar") * Decimal(f.numerator) / Decimal(f.denominator)).quantize(Decimal(1))
        if amt > 0:
            self._post("collect_ar", amt)

    @rule(a=amounts)
    def buy(self, a):
        self._post("buy_inventory_on_credit", a)

    @precondition(lambda s: s._bal("inventory") > 0)
    @rule()
    def ship_all(self):
        self._post("ship_goods", self._bal("inventory"))

    @precondition(lambda s: s._bal("ap") > 0 and s._bal("cash") >= s._bal("ap"))
    @rule()
    def pay_all_ap(self):
        self._post("pay_ap", self._bal("ap"))

    @precondition(lambda s: s._bal("cash") > 10)
    @rule()
    def capex(self):
        self._post("capex", "10")

    @precondition(lambda s: s._bal("ppe") - s._bal("acc_dep") >= 1)
    @rule()
    def depreciate(self):
        self._post("depreciate", "1")

    @precondition(lambda s: s.p < 12)
    @rule()
    def close_period(self):
        self.led.close(self.p, f"2025-{self.p:02d}-28T00:00:00Z")
        self.p += 1

    @invariant()
    def identities_hold(self):
        bs = L.balance_sheet(self.led, self.p)
        assert bs["total_assets"] == bs["total_liabilities"] + bs["total_equity"]
        assert L.cash_flow_direct(self.led, self.p) == L.cash_flow_indirect(self.led, self.p)
        assert bs["cash"] >= 0 and bs["inventory"] >= 0 and bs["ar"] >= 0


TestLedgerMachine = LedgerMachine.TestCase
TestLedgerMachine.settings = machine_settings(max_examples=60, stateful_step_count=40)
