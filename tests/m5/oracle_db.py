"""Isolated ``python-accounting`` database/session construction.

The oracle uses SQLite in memory. Each sequence gets its own engine, schema,
entity, currency, and chart, so one sequence can never observe another's rows
and the run needs no database service.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from python_accounting.database.session import get_session
from python_accounting.models import Account, Base, Currency, Entity
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from tests.m5.chart import FWF_ACCOUNTS, ORACLE_ACCOUNT_TYPE
from tests.m5.oracle_side import OracleCompany


@contextmanager
def oracle_company(entity_id: str) -> Iterator[OracleCompany]:
    """Yield a fresh, isolated ``python-accounting`` company."""
    engine: Engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with get_session(engine) as session:
        entity = Entity(name=entity_id)
        session.add(entity)
        session.commit()
        currency = Currency(name="US Dollar", code="USD", entity_id=entity.id)
        session.add(currency)
        session.commit()
        account_type = Account.AccountType
        accounts: dict[str, Account] = {}
        for name in FWF_ACCOUNTS:
            account = Account(
                name=name,
                account_type=getattr(account_type, ORACLE_ACCOUNT_TYPE[name]),
                currency_id=currency.id,
                entity_id=entity.id,
            )
            session.add(account)
            accounts[name] = account
        session.commit()
        yield OracleCompany(session, entity, currency, accounts)
