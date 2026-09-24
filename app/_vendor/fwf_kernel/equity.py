"""Single-class equity accounting, share events, WASO, and basic EPS for M4.2A.

The ledger remains authoritative for monetary journal entries and statement formulas. This
module owns only common-share state and deterministic share/EPS derivations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, DecimalException, localcontext
from enum import StrEnum
from fractions import Fraction
from typing import Final

from . import ledger as L
from .temporal import canonical_utc_instant

CENT: Final = Decimal("0.01")
ZERO: Final = Decimal("0.00")
EPS_QUANTUM: Final = Decimal("0.000001")

EQUITY_RULE: Final = "ACCT-EQUITY-SUBLEDGER"
WASO_RULE: Final = "ACCT-WASO"
BASIC_EPS_RULE: Final = "ACCT-BASIC-EPS"

RECONCILIATION_RULES: Final[dict[str, str]] = {
    EQUITY_RULE: "equity events reconcile common stock, APIC, treasury stock, and share counts",
    WASO_RULE: "weighted-average shares equal exact daily share-day weighting",
    BASIC_EPS_RULE: "basic EPS is recomputed from net income and weighted-average shares",
}

type DecimalSource = str | int | Decimal
type ShareCountSource = int | str | Decimal
type FactorSource = int | Fraction | tuple[int, int] | str


def _exact_decimal(
    value: DecimalSource, field_name: str, *, allow_negative: bool = False
) -> Decimal:
    if isinstance(value, (bool, float)):
        raise TypeError("binary floats are not accepted in equity state")
    try:
        amount = Decimal(value)
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"invalid decimal {field_name}: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if not allow_negative and amount < ZERO:
        raise ValueError(f"{field_name} must be non-negative")
    return amount


def _money(value: DecimalSource, *, allow_negative: bool = False) -> Decimal:
    amount = _exact_decimal(value, "money", allow_negative=allow_negative)
    try:
        with localcontext() as context:
            context.prec = 60
            return amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ValueError("money cannot be represented at cent precision") from exc


def _decimal_from_fraction(value: Fraction) -> Decimal:
    if value < 0:
        raise ValueError("fraction must be non-negative")
    try:
        with localcontext() as context:
            context.prec = 60
            return Decimal(value.numerator) / Decimal(value.denominator)
    except DecimalException as exc:
        raise ValueError("fraction cannot be represented as Decimal") from exc


def _money_from_fraction(value: Fraction) -> Decimal:
    return _money(_decimal_from_fraction(value))


def _count(value: ShareCountSource, field_name: str) -> int:
    if isinstance(value, (bool, float)):
        raise TypeError("share counts must be integers, not binary floats")
    try:
        numeric = Decimal(value)
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"invalid share count {field_name}: {value!r}") from exc
    if not numeric.is_finite() or numeric < 0 or numeric != numeric.to_integral_value():
        raise ValueError(f"{field_name} must be a non-negative integer")
    return int(numeric)


def _factor(value: FactorSource) -> Fraction:
    if isinstance(value, (bool, float)):
        raise TypeError("split factors must use exact integers or fractions")
    try:
        result = Fraction(value[0], value[1]) if isinstance(value, tuple) else Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("split factor must be an exact positive fraction") from exc
    if result <= 0:
        raise ValueError("split factor must be positive")
    return result


def _date(value: date, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date, not a datetime or string")
    return value


def _period(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be an operating period >= 1")


def _validate_effective_event_timing(
    effective_date: date,
    event_time: str | None,
    posted_at: str | None,
    *,
    timestamps_required: bool,
) -> None:
    """Validate the deterministic share-count/ledger clock relationship."""
    _date(effective_date, "effective_date")
    if event_time is None and posted_at is None:
        if timestamps_required:
            raise ValueError("issuance and repurchase require event_time and posted_at")
        return
    if event_time is None or posted_at is None:
        raise ValueError("event_time and posted_at must both be present or both be absent")
    event = canonical_utc_instant(event_time)
    posted = canonical_utc_instant(posted_at)
    if event > posted:
        raise ValueError("require event_time <= posted_at")
    if event.date() != effective_date:
        raise ValueError("effective_date must equal the UTC calendar date of event_time")


class ShareEventKind(StrEnum):
    ISSUANCE = "issuance"
    REPURCHASE = "repurchase"
    SPLIT = "split"


@dataclass(frozen=True, slots=True)
class CommonShareClass:
    """Immutable description of the single common-stock class."""

    class_id: str
    par_value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.class_id, str) or not self.class_id.strip():
            raise ValueError("class_id must be a non-empty string")
        par = _exact_decimal(self.par_value, "par_value")
        object.__setattr__(self, "par_value", par)


@dataclass(frozen=True, slots=True)
class ShareEvent:
    """One append-only share event; split events carry an exact rational factor."""

    event_id: str
    kind: ShareEventKind
    effective_date: date
    period: int
    shares: int = 0
    price: Decimal = ZERO
    factor: Fraction = Fraction(1, 1)
    event_time: str | None = None
    posted_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.kind, ShareEventKind):
            raise TypeError("kind must be a ShareEventKind")
        _date(self.effective_date, "effective_date")
        _period(self.period, "period")
        if isinstance(self.shares, bool) or not isinstance(self.shares, int):
            raise TypeError("shares must be an integer")
        price = _exact_decimal(self.price, "price")
        object.__setattr__(self, "price", price)
        if not isinstance(self.factor, Fraction) or self.factor <= 0:
            raise ValueError("factor must be a positive Fraction")
        if self.kind is ShareEventKind.SPLIT:
            if self.shares != 0:
                raise ValueError("split events cannot carry share counts")
        elif self.shares <= 0:
            raise ValueError(f"{self.kind.value} events require positive shares")
        _validate_effective_event_timing(
            self.effective_date,
            self.event_time,
            self.posted_at,
            timestamps_required=self.kind is not ShareEventKind.SPLIT,
        )


@dataclass(frozen=True, slots=True)
class ShareCounts:
    issued_shares: int
    treasury_shares: int
    outstanding_shares: int


@dataclass(frozen=True, slots=True)
class OpeningEquityState:
    """Opening share counts and monetary equity that must agree with period-0 GL."""

    issued_shares: int
    treasury_shares: int
    common_stock: Decimal
    additional_paid_in_capital: Decimal
    treasury_stock: Decimal

    def __post_init__(self) -> None:
        issued = _count(self.issued_shares, "issued_shares")
        treasury = _count(self.treasury_shares, "treasury_shares")
        if treasury > issued:
            raise ValueError("treasury_shares cannot exceed issued_shares")
        object.__setattr__(self, "issued_shares", issued)
        object.__setattr__(self, "treasury_shares", treasury)
        object.__setattr__(self, "common_stock", _money(self.common_stock))
        object.__setattr__(
            self, "additional_paid_in_capital", _money(self.additional_paid_in_capital)
        )
        object.__setattr__(self, "treasury_stock", _money(self.treasury_stock))


class EquityBook:
    """Own common-share events and reconcile their monetary ledger controls."""

    def __init__(
        self,
        ledger: L.Ledger,
        share_class: CommonShareClass,
        *,
        issued_shares: ShareCountSource | None = None,
        treasury_shares: ShareCountSource = 0,
        opening_common_stock: DecimalSource | None = None,
        opening_apic: DecimalSource | None = None,
        opening_treasury_stock: DecimalSource | None = None,
        opening_state: OpeningEquityState | None = None,
    ) -> None:
        common: DecimalSource
        apic: DecimalSource
        treasury_stock: DecimalSource
        if opening_state is not None:
            if issued_shares is not None:
                raise ValueError("use opening_state or issued_shares, not both")
            if (
                opening_common_stock is not None
                or opening_apic is not None
                or opening_treasury_stock is not None
            ):
                raise ValueError("opening monetary values must be supplied through opening_state")
            issued = opening_state.issued_shares
            treasury = opening_state.treasury_shares
            common = opening_state.common_stock
            apic = opening_state.additional_paid_in_capital
            treasury_stock = opening_state.treasury_stock
        else:
            if issued_shares is None:
                raise ValueError("issued_shares is required for an equity book")
            issued = _count(issued_shares, "issued_shares")
            treasury = _count(treasury_shares, "treasury_shares")
            balances = ledger.balances()
            common = (
                balances["common_stock"] if opening_common_stock is None else opening_common_stock
            )
            apic = balances["additional_paid_in_capital"] if opening_apic is None else opening_apic
            treasury_stock = (
                balances["treasury_stock"]
                if opening_treasury_stock is None
                else opening_treasury_stock
            )
        opening_issued = _count(issued, "issued_shares")
        opening_treasury = _count(treasury, "treasury_shares")
        if opening_treasury > opening_issued:
            raise ValueError("treasury_shares cannot exceed issued_shares")
        self.ledger = ledger
        self.share_class = share_class
        self._opening_issued_shares = opening_issued
        self._opening_treasury_shares = opening_treasury
        self._issued_shares = opening_issued
        self._treasury_shares = opening_treasury
        self._par_fraction = Fraction(share_class.par_value)
        self._common_stock_balance = _money(common)
        self._apic_balance = _money(apic)
        self._treasury_stock_balance = _money(treasury_stock)
        self._events: list[ShareEvent] = []
        self._entry_sequence = self._last_entry_sequence()
        self._require_valid()

    def _last_entry_sequence(self) -> int:
        sequences = []
        for entry in self.ledger.entries:
            parts = entry.entry_id.split("-", 2)
            if len(parts) == 3 and parts[0] == "m42a" and parts[1].isdigit():
                sequences.append(int(parts[1]))
        return max(sequences, default=0)

    def _next_event_id(self) -> str:
        return f"m42a-event-{len(self._events) + 1:08d}"

    def _require_valid(self) -> None:
        failures = self.validate_reconciliations()
        if failures:
            raise ValueError(f"invalid equity book: {sorted(failures)}")

    def _ordered_events(self, cutoff: date | None = None) -> list[ShareEvent]:
        priority = {
            ShareEventKind.SPLIT: 0,
            ShareEventKind.ISSUANCE: 1,
            ShareEventKind.REPURCHASE: 2,
        }
        indexed = list(enumerate(self._events))
        indexed.sort(key=lambda pair: (pair[1].effective_date, priority[pair[1].kind], pair[0]))
        return [event for _, event in indexed if cutoff is None or event.effective_date <= cutoff]

    @staticmethod
    def _apply_event(
        issued: int,
        treasury: int,
        event: ShareEvent,
    ) -> tuple[int, int]:
        if event.kind is ShareEventKind.ISSUANCE:
            issued += event.shares
        elif event.kind is ShareEventKind.REPURCHASE:
            treasury += event.shares
            if treasury > issued:
                raise ValueError("repurchased shares exceed issued shares")
        else:
            numerator = event.factor.numerator
            denominator = event.factor.denominator
            if (issued * numerator) % denominator or (treasury * numerator) % denominator:
                raise ValueError("split would produce fractional shares")
            issued = issued * numerator // denominator
            treasury = treasury * numerator // denominator
        return issued, treasury

    def _replay_counts(self, cutoff: date | None = None) -> ShareCounts:
        issued = self._opening_issued_shares
        treasury = self._opening_treasury_shares
        for event in self._ordered_events(cutoff):
            issued, treasury = self._apply_event(issued, treasury, event)
        return ShareCounts(issued, treasury, issued - treasury)

    def share_counts(self, as_of_date: date | None = None) -> ShareCounts:
        """Derive issued, treasury, and outstanding shares as of a date."""
        if as_of_date is not None:
            _date(as_of_date, "as_of_date")
        return self._replay_counts(as_of_date)

    @property
    def issued_shares(self) -> int:
        return self._issued_shares

    @property
    def treasury_shares(self) -> int:
        return self._treasury_shares

    @property
    def outstanding_shares(self) -> int:
        return self._issued_shares - self._treasury_shares

    @property
    def par_value(self) -> Decimal:
        return _decimal_from_fraction(self._par_fraction)

    @property
    def events(self) -> tuple[ShareEvent, ...]:
        return tuple(self._events)

    def validate_reconciliations(
        self,
        *,
        period_start: date | None = None,
        period_end: date | None = None,
        as_of_date: date | None = None,
        net_income: DecimalSource | None = None,
        reported_waso: DecimalSource | None = None,
        reported_eps: DecimalSource | None = None,
    ) -> set[str]:
        """Return broken M4.2A rules, optionally checking a reported WASO/EPS value."""
        failures: set[str] = set()
        try:
            counts = self._replay_counts()
            if (
                counts.issued_shares != self._issued_shares
                or counts.treasury_shares != self._treasury_shares
                or counts.outstanding_shares != self.outstanding_shares
                or self._issued_shares < self._treasury_shares
                or self._treasury_shares < 0
            ):
                raise ValueError("share-count state mismatch")
            if (
                Fraction(self._common_stock_balance)
                != Fraction(counts.issued_shares) * self._par_fraction
            ):
                raise ValueError("common stock does not equal issued shares times par")
            balances = self.ledger.balances()
            if (
                balances["common_stock"] != self._common_stock_balance
                or balances["additional_paid_in_capital"] != self._apic_balance
                or balances["treasury_stock"] != self._treasury_stock_balance
            ):
                raise ValueError("equity control mismatch")
            event_ids: set[str] = set()
            for event in self._events:
                if event.event_id in event_ids:
                    raise ValueError("duplicate share event")
                event_ids.add(event.event_id)
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            failures.add(EQUITY_RULE)

        if (period_start is None) != (period_end is None):
            failures.add(WASO_RULE)
        elif period_start is not None and period_end is not None:
            failures.update(self.validate_waso(period_start, period_end, as_of_date, reported_waso))
            if net_income is not None:
                failures.update(
                    self.validate_basic_eps(
                        net_income, period_start, period_end, as_of_date, reported_eps
                    )
                )
        return failures

    def _validate_date_range(
        self, period_start: date, period_end: date, as_of_date: date | None
    ) -> date:
        start = _date(period_start, "period_start")
        end = _date(period_end, "period_end")
        if end < start:
            raise ValueError("period_end must be >= period_start")
        cutoff = date.max if as_of_date is None else _date(as_of_date, "as_of_date")
        if cutoff < end:
            raise ValueError("as_of_date must be >= period_end")
        return cutoff

    def _outstanding_on(self, day: date, cutoff: date) -> int:
        counts = self._replay_counts(min(cutoff, day))
        return counts.outstanding_shares

    def weighted_average_shares(
        self,
        period_start: date,
        period_end: date,
        *,
        as_of_date: date | None = None,
    ) -> Decimal:
        """Return exact inclusive daily share-day weighting as a Decimal."""
        cutoff = self._validate_date_range(period_start, period_end, as_of_date)
        total_days = (period_end - period_start).days + 1
        share_days = Fraction(0)
        day = period_start
        while day <= period_end:
            outstanding = self._outstanding_on(day, cutoff)
            adjusted = Fraction(outstanding)
            for event in self._ordered_events(cutoff):
                if event.kind is ShareEventKind.SPLIT and day < event.effective_date:
                    adjusted *= event.factor
            share_days += adjusted
            day += timedelta(days=1)
        return _decimal_from_fraction(share_days / total_days)

    def validate_waso(
        self,
        period_start: date,
        period_end: date,
        as_of_date: date | None = None,
        reported: DecimalSource | None = None,
    ) -> set[str]:
        try:
            expected = self.weighted_average_shares(period_start, period_end, as_of_date=as_of_date)
            if reported is not None and _exact_decimal(reported, "reported WASO") != expected:
                return {WASO_RULE}
        except (ArithmeticError, TypeError, ValueError):
            return {WASO_RULE}
        return set()

    def basic_eps(
        self,
        net_income: DecimalSource,
        period_start: date,
        period_end: date,
        *,
        as_of_date: date | None = None,
    ) -> Decimal:
        income = _money(net_income, allow_negative=True)
        waso = self.weighted_average_shares(period_start, period_end, as_of_date=as_of_date)
        if waso <= ZERO:
            raise ValueError("basic EPS denominator must be positive")
        try:
            with localcontext() as context:
                context.prec = 60
                return (income / waso).quantize(EPS_QUANTUM, rounding=ROUND_HALF_UP)
        except DecimalException as exc:
            raise ValueError("basic EPS cannot be represented") from exc

    def validate_basic_eps(
        self,
        net_income: DecimalSource,
        period_start: date,
        period_end: date,
        as_of_date: date | None = None,
        reported: DecimalSource | None = None,
    ) -> set[str]:
        try:
            expected = self.basic_eps(net_income, period_start, period_end, as_of_date=as_of_date)
            if reported is not None and _exact_decimal(reported, "reported EPS") != expected:
                return {BASIC_EPS_RULE}
        except (ArithmeticError, TypeError, ValueError):
            return {BASIC_EPS_RULE}
        return set()

    def _post_event(
        self,
        event: ShareEvent,
        amount: Decimal | dict[str, Decimal],
        event_time: str,
        posted_at: str,
    ) -> None:
        candidate = self._entry_sequence + 1
        ledger_event = {
            ShareEventKind.ISSUANCE: "issue_common_shares",
            ShareEventKind.REPURCHASE: "repurchase_common_shares",
        }[event.kind]
        entry_id = f"m42a-{candidate:08d}-{ledger_event}"
        self.ledger.post(
            L.event(
                entry_id,
                self.ledger.entity_id,
                event.period,
                ledger_event,
                amount,
                event_time,
                posted_at,
            )
        )
        self._entry_sequence = candidate

    def issue_common_shares(
        self,
        shares: ShareCountSource,
        issue_price: DecimalSource,
        effective_date: date,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> ShareEvent:
        self._require_valid()
        count = _count(shares, "shares")
        if count <= 0:
            raise ValueError("share issuance count must be positive")
        _date(effective_date, "effective_date")
        _period(period, "period")
        _validate_effective_event_timing(
            effective_date,
            event_time,
            posted_at,
            timestamps_required=True,
        )
        price = _exact_decimal(issue_price, "issue_price")
        price_fraction = Fraction(price)
        if price_fraction < self._par_fraction:
            raise ValueError("issue price must be at least par value")
        proceeds_fraction = price_fraction * count
        par_fraction = self._par_fraction * count
        proceeds = _money_from_fraction(proceeds_fraction)
        par_capital = _money_from_fraction(par_fraction)
        apic = _money_from_fraction(proceeds_fraction - par_fraction)
        if apic < ZERO or par_capital > proceeds:
            raise ValueError("share issuance cannot be represented at cent precision")
        event = ShareEvent(
            self._next_event_id(),
            ShareEventKind.ISSUANCE,
            effective_date,
            period,
            shares=count,
            price=price,
            event_time=event_time,
            posted_at=posted_at,
        )
        prospective_common = self._common_stock_balance + par_capital
        if Fraction(prospective_common) != (self._issued_shares + count) * self._par_fraction:
            raise ValueError("share issuance cannot preserve exact common-stock product")
        self._post_event(
            event,
            {
                "cash": proceeds,
                "common_stock": par_capital,
                "additional_paid_in_capital": apic,
            },
            event_time,
            posted_at,
        )
        self._issued_shares += count
        self._common_stock_balance += par_capital
        self._apic_balance += apic
        self._events.append(event)
        return event

    def repurchase_common_shares(
        self,
        shares: ShareCountSource,
        price_per_share: DecimalSource,
        effective_date: date,
        period: int,
        event_time: str,
        posted_at: str,
    ) -> ShareEvent:
        self._require_valid()
        count = _count(shares, "shares")
        if count <= 0:
            raise ValueError("repurchase count must be positive")
        _date(effective_date, "effective_date")
        _period(period, "period")
        _validate_effective_event_timing(
            effective_date,
            event_time,
            posted_at,
            timestamps_required=True,
        )
        price = _exact_decimal(price_per_share, "price_per_share")
        if price <= ZERO:
            raise ValueError("repurchase price must be positive")
        cost = _money_from_fraction(Fraction(price) * count)
        if count > self.outstanding_shares:
            raise ValueError("repurchase exceeds outstanding shares")
        if self.ledger.balances()["cash"] < cost:
            raise ValueError("insufficient cash for share repurchase")
        event = ShareEvent(
            self._next_event_id(),
            ShareEventKind.REPURCHASE,
            effective_date,
            period,
            shares=count,
            price=price,
            event_time=event_time,
            posted_at=posted_at,
        )
        self._post_event(
            event,
            {"treasury_stock": cost, "cash": cost},
            event_time,
            posted_at,
        )
        self._treasury_shares += count
        self._treasury_stock_balance += cost
        self._events.append(event)
        return event

    def split_common_shares(
        self,
        factor: FactorSource,
        effective_date: date,
        period: int,
        event_time: str | None = None,
        posted_at: str | None = None,
    ) -> ShareEvent:
        """Change share counts and par value without posting a journal entry."""
        self._require_valid()
        split_factor = _factor(factor)
        _date(effective_date, "effective_date")
        _period(period, "period")
        _validate_effective_event_timing(
            effective_date,
            event_time,
            posted_at,
            timestamps_required=False,
        )
        numerator = split_factor.numerator
        denominator = split_factor.denominator
        if (self._issued_shares * numerator) % denominator or (
            self._treasury_shares * numerator
        ) % denominator:
            raise ValueError("split would produce fractional shares")
        event = ShareEvent(
            self._next_event_id(),
            ShareEventKind.SPLIT,
            effective_date,
            period,
            factor=split_factor,
            event_time=event_time,
            posted_at=posted_at,
        )
        self._issued_shares = self._issued_shares * numerator // denominator
        self._treasury_shares = self._treasury_shares * numerator // denominator
        self._par_fraction *= Fraction(denominator, numerator)
        self._events.append(event)
        return event
