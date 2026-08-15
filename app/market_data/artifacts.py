"""Frozen market-data artifacts (Phase 3).

Every successful data acquisition produces a frozen panel artifact with
manifest. Runs reference the dataset digest; campaigns reuse frozen panels
without reacquiring data.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.evidence.artifact_store import ArtifactStore
from app.market_data.digest import compute_dataset_digest
from app.market_data.errors import DatasetDigestMismatchError
from app.market_data.panel import MarketDataPanel
from app.market_data.quality import DataQualityReport

SCHEMA_VERSION = "market-data-artifact/v3.0"


def _register_dataset(
    session,
    *,
    project_id: str,
    canonical_digest: str,
    provider: str,
    provider_version: str,
    request_json: dict[str, Any],
    provenance_json: dict[str, Any],
    quality_json: dict[str, Any],
    artifact_manifest_ref: str,
) -> None:
    """Idempotently register a frozen dataset for (project_id, canonical_digest).

    Identity is an independent ``ds_<uuid4 hex>`` row id -- NOT the digest -- so
    two different projects that legitimately freeze the identical panel get
    distinct primary keys while the semantic uniqueness lives in the
    ``uq_datasets_project_digest`` constraint.

    Concurrency: two simultaneous freezes of the same (project, digest) can both
    observe "absent", both attempt the INSERT, and the loser hits the unique
    constraint. We catch that :class:`IntegrityError`, roll back to a savepoint,
    and return the row the winner committed. No HTTP 500, no duplicate row.
    """
    from app.persistence.models import DatasetRow

    try:
        with session.begin_nested():  # SAVEPOINT isolates the race from caller txn
            session.add(
                DatasetRow(
                    dataset_id=f"ds_{uuid.uuid4().hex}",
                    project_id=project_id,
                    canonical_digest=canonical_digest,
                    provider=provider,
                    provider_version=provider_version,
                    request_json=request_json,
                    provenance_json=provenance_json,
                    quality_json=quality_json,
                    artifact_manifest_ref=artifact_manifest_ref,
                )
            )
    except IntegrityError:
        # The duplicate INSERT rolled back the SAVEPOINT automatically (the
        # enclosing transaction is preserved). Another concurrent freeze won the
        # (project_id, canonical_digest) slot -- resolve to its row. We do NOT
        # call session.rollback() here: that would roll back the OUTER
        # transaction and erase unrelated work the caller already staged.
        existing = (
            session.query(DatasetRow)
            .filter_by(project_id=project_id, canonical_digest=canonical_digest)
            .one_or_none()
        )
        if existing is None:
            # Not a duplicate-row race: a real FK violation (project_id missing).
            # Re-raise so the caller sees the genuine constraint failure.
            raise
        # Duplicate-row race resolved: the row already exists. Idempotent success.
        return existing
    return None


def freeze_panel(
    panel: MarketDataPanel,
    quality: DataQualityReport,
    request: Any,
    store: ArtifactStore,
    run_id: str,
    session: Any = None,
) -> dict[str, Any]:
    """Persist a canonical panel as frozen artifacts.

    Returns the manifest dict. All artifacts are indexed in the store.

    When ``session`` is provided, also registers a ``DatasetRow`` (Phase 3 D5)
    so the run can later prove it referenced this exact frozen dataset, and
    stamps ``RunRow.dataset_digest``.
    """
    from app.strategy_lab.canonical.durable import write_artifact

    artifacts: list[dict[str, Any]] = []

    # Request (only when a request object is supplied; tests may freeze a panel
    # without a request object, in which case the request artifact is skipped).
    if request is not None:
        write_artifact(
            session=session,
            store=store,
            run_id=run_id,
            key=f"runs/{run_id}/market-data-request.json",
            payload=_request_to_dict(request),
            artifact_index=artifacts,
        )

    # Panel (deterministic columnar representation)
    write_artifact(
        session=session,
        store=store,
        run_id=run_id,
        key=f"runs/{run_id}/market-data-panel.json",
        payload=panel.to_dict(),
        artifact_index=artifacts,
    )

    # Provenance
    write_artifact(
        session=session,
        store=store,
        run_id=run_id,
        key=f"runs/{run_id}/market-data-provenance.json",
        payload={
            "provider": panel.provider,
            "provider_version": panel.provider_version,
            "retrieval_timestamp": panel.retrieval_timestamp.isoformat(),
            "as_of": panel.as_of.isoformat() if panel.as_of else None,
            "calendar_policy": panel.calendar_policy.value,
            "adjustment_policy": panel.adjustment_policy.value,
            "missing_data_policy": panel.missing_data_policy,
            "eligibility_source": panel.eligibility_source.value,
            "source_metadata": panel.source_metadata,
        },
        artifact_index=artifacts,
    )

    # Quality report
    write_artifact(
        session=session,
        store=store,
        run_id=run_id,
        key=f"runs/{run_id}/market-data-quality-report.json",
        payload=quality.to_dict(),
        artifact_index=artifacts,
    )

    # Manifest
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_digest": panel.dataset_digest,
        "provider": panel.provider,
        "provider_version": panel.provider_version,
        "instrument_count": panel.N,
        "bar_count": panel.T,
        "artifacts": artifacts,
    }
    write_artifact(
        session=session,
        store=store,
        run_id=run_id,
        key=f"runs/{run_id}/market-data-manifest.json",
        payload=manifest,
        artifact_index=artifacts,
    )

    # Persist the dataset registry row + link the run (Phase 3 D5), if a session
    # is available. Falls back silently to artifacts-only when no session is
    # passed (caller is responsible for persistence in that case).
    if session is not None:
        from app.persistence.models import RunRow

        project_id = None
        run_row = session.get(RunRow, run_id)
        if run_row is not None:
            project_id = run_row.project_id

        if project_id is not None:
            request_json = _request_to_dict(request)
            provenance_json = {
                "provider": panel.provider,
                "provider_version": panel.provider_version,
                "retrieval_timestamp": panel.retrieval_timestamp.isoformat(),
                "as_of": panel.as_of.isoformat() if panel.as_of else None,
                "calendar_policy": panel.calendar_policy.value,
                "adjustment_policy": panel.adjustment_policy.value,
                "missing_data_policy": panel.missing_data_policy,
                "eligibility_source": panel.eligibility_source.value,
                "source_metadata": panel.source_metadata,
            }
            _register_dataset(
                session,
                project_id=project_id,
                canonical_digest=panel.dataset_digest,
                provider=panel.provider,
                provider_version=panel.provider_version,
                request_json=request_json,
                provenance_json=provenance_json,
                quality_json=quality.to_dict(),
                artifact_manifest_ref=f"runs/{run_id}/market-data-manifest.json",
            )
        if run_row is not None:
            run_row.dataset_digest = panel.dataset_digest

    return manifest


def load_frozen_panel(
    store: ArtifactStore,
    run_id: str,
    expected_digest: str | None = None,
) -> tuple[MarketDataPanel, dict[str, Any]]:
    """Reload a frozen panel and verify its digest.

    Raises DatasetDigestMismatchError if the stored digest does not match
    the recomputed digest or the expected digest.
    """
    from app.strategy_lab.canonical.durable import read_artifact

    manifest = read_artifact(store, run_id=run_id, key=f"runs/{run_id}/market-data-manifest.json")
    panel_dict = read_artifact(store, run_id=run_id, key=f"runs/{run_id}/market-data-panel.json")

    panel = MarketDataPanel.from_dict(panel_dict)
    recomputed = compute_dataset_digest(panel)

    if recomputed != panel.dataset_digest:
        raise DatasetDigestMismatchError(f"stored digest {panel.dataset_digest} != recomputed {recomputed}")
    if expected_digest is not None and panel.dataset_digest != expected_digest:
        raise DatasetDigestMismatchError(
            f"expected digest {expected_digest} != stored {panel.dataset_digest}"
        )
    if manifest["dataset_digest"] != panel.dataset_digest:
        raise DatasetDigestMismatchError(
            f"manifest digest {manifest['dataset_digest']} != panel digest {panel.dataset_digest}"
        )

    return panel, manifest


def _request_to_dict(request: Any) -> dict[str, Any]:
    """Serialize MarketDataRequest for artifact storage."""
    return {
        "instruments": [
            {
                "symbol": i.symbol,
                "venue": i.venue,
                "asset_type": i.asset_type.value,
                "currency": i.currency,
                "provider_symbol": i.provider_symbol,
                "stable_id": i.stable_id,
            }
            for i in request.instruments
        ],
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "frequency": request.frequency.value,
        "required_fields": list(request.required_fields),
        "calendar_policy": request.calendar_policy.value,
        "adjustment_policy": request.adjustment_policy.value,
        "missing_data_policy": request.missing_data_policy,
        "benchmark": (
            {
                "symbol": request.benchmark.symbol,
                "venue": request.benchmark.venue,
                "asset_type": request.benchmark.asset_type.value,
                "currency": request.benchmark.currency,
                "provider_symbol": request.benchmark.provider_symbol,
                "stable_id": request.benchmark.stable_id,
            }
            if request.benchmark
            else None
        ),
        "benchmark_tradable": request.benchmark_tradable,
        "eligibility_source": request.eligibility_source.value,
        "as_of": request.as_of.isoformat() if request.as_of else None,
        "provider_name": request.provider_name,
        "provider_config_version": request.provider_config_version,
        "allow_synthetic_fixture": request.allow_synthetic_fixture,
        "max_forward_fill_gap": request.max_forward_fill_gap,
        "extra": request.extra,
    }


__all__ = ["freeze_panel", "load_frozen_panel", "SCHEMA_VERSION"]
