"""Phase 5 disjoint-evidence: PostgreSQL-specific invariant (#8).

The EFFECTIVE-WORLD identity uniqueness ``(campaign_id, world_hash)`` is a
relational invariant that must hold on real PostgreSQL (not just SQLite). This
test proves the DB itself rejects a duplicate effective-world identity within a
campaign -- the backstop behind the application-level disjointness enforcement.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("psycopg")

PG_URL = os.environ.get("FENRIX_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL, reason="FENRIX_TEST_POSTGRES_URL not set; skipping real-Postgres tests"
)

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.persistence.database import (  # noqa: E402
    create_all,
    make_engine,
    make_session_factory,
    session_scope,
)
from app.persistence.models import (  # noqa: E402
    Base,
    CampaignRow,
    Project,
    RunRow,
    ScenarioWorldRow,
    Strategy,
    StrategyVersionRow,
)


@pytest.fixture()
def pg_factory():
    engine = make_engine(PG_URL)
    Base.metadata.drop_all(engine)
    create_all(engine)
    yield make_session_factory(engine)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _make_campaign(s) -> str:
    """Build the full FK chain (project -> strategy -> strategy_version -> run ->
    campaign) and return the new campaign_id. The world_hash UNIQUE constraint is
    the thing under test, but the row can only be inserted once its FK chain
    (run_id -> runs.id) actually exists -- on real PostgreSQL a dangling run_id
    fails the FK before the uniqueness assertion is ever reached."""
    project_id = "p1"
    strategy_id = "s1"
    run_id = str(uuid.uuid4())
    if s.get(Project, project_id) is None:
        s.add(Project(id=project_id, name="WS"))
    if s.get(Strategy, strategy_id) is None:
        s.add(Strategy(id=strategy_id, project_id=project_id, name="S"))
    if s.get(StrategyVersionRow, 1) is None:
        s.add(
            StrategyVersionRow(
                strategy_id=strategy_id,
                version=1,
                canonical_hash="0" * 64,
                canonical_json="{}",
                state="approved",
            )
        )
        s.flush()
    if s.get(RunRow, run_id) is None:
        s.add(
            RunRow(
                id=run_id,
                project_id=project_id,
                strategy_id=strategy_id,
                strategy_version=1,
                strategy_hash="0" * 64,
                data_mode="demo_fixture",
            )
        )
    camp_id = str(uuid.uuid4())
    s.add(
        CampaignRow(
            id=camp_id,
            run_id=run_id,
            project_id=project_id,
            strategy_id=strategy_id,
            strategy_version=1,
            strategy_hash="0" * 64,
            base_panel_digest="d" * 64,
            mechanisms=["vol_spike"],
            seeds=[1],
            failure_predicates=[],
            confirmation_policy={},
        )
    )
    s.flush()
    return camp_id


def test_pg_unique_effective_world_identity_rejected(pg_factory):
    """Two scenario worlds with the SAME (campaign_id, world_hash) must be
    rejected by PostgreSQL -- the DB-level backstop guaranteeing a confirmation
    world can never reuse the primary world's identity."""
    with session_scope(pg_factory) as s:
        camp_id = _make_campaign(s)
        s.add(
            ScenarioWorldRow(
                id=str(uuid.uuid4()),
                campaign_id=camp_id,
                world_key="primary",
                mechanism="vol_spike",
                seed=1,
                intensity=0.3,
                definition={"mechanism": "vol_spike"},
                content_digest="c" * 64,
                world_hash="W" * 64,
                diagnostics={},
            )
        )
        s.flush()
        # A confirmation world that reuses the primary's effective identity.
        s.add(
            ScenarioWorldRow(
                id=str(uuid.uuid4()),
                campaign_id=camp_id,
                world_key="confirm",
                mechanism="vol_spike",
                seed=2,
                intensity=0.3,
                definition={"mechanism": "vol_spike"},
                content_digest="c2" * 32,
                world_hash="W" * 64,  # <-- duplicate effective identity
                diagnostics={},
            )
        )
        # Postgres must reject the duplicate (unique constraint backstop).
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_pg_distinct_effective_world_identities_allowed(pg_factory):
    """Two scenario worlds with DISTINCT effective identities are fine."""
    with session_scope(pg_factory) as s:
        camp_id = _make_campaign(s)
        s.add(
            ScenarioWorldRow(
                id=str(uuid.uuid4()),
                campaign_id=camp_id,
                world_key="primary",
                mechanism="vol_spike",
                seed=1,
                intensity=0.3,
                definition={"mechanism": "vol_spike"},
                content_digest="c" * 64,
                world_hash="W" * 64,
                diagnostics={},
            )
        )
        s.add(
            ScenarioWorldRow(
                id=str(uuid.uuid4()),
                campaign_id=camp_id,
                world_key="confirm",
                mechanism="vol_spike",
                seed=2,
                intensity=0.3,
                definition={"mechanism": "vol_spike"},
                content_digest="c2" * 32,
                world_hash="X" * 64,  # distinct
                diagnostics={},
            )
        )
        s.commit()  # must succeed
