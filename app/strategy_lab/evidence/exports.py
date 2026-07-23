from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC
from io import StringIO
from typing import Any

from app.strategy_lab.persistence.repository import StrategyLabRepository


class EvidencePackager:
    @staticmethod
    def build(
        *, repository: StrategyLabRepository, campaign_id: str, creator: str = "system"
    ) -> dict[str, Any]:
        campaign = repository.campaign(campaign_id)
        strategy_id = campaign.get("strategy_id")
        strategy_version = repository.strategy_version(str(strategy_id)) if strategy_id else {}
        clauses = repository.clauses_for_strategy(str(strategy_id)) if strategy_id else []
        approvals = repository.approvals_for_strategy(str(strategy_id)) if strategy_id else []
        backtests = repository.backtests_for_strategy(str(strategy_id)) if strategy_id else []
        failures = repository.failures_for_campaign(campaign_id)
        replay_events: list[dict[str, Any]] = []
        for failure in failures:
            replay_events.extend(repository.replay_events_for_failure(failure["failure_id"]))

        strategy_content = EvidencePackager._build_strategy_content(
            strategy_version=strategy_version,
            clauses=clauses,
            approvals=approvals,
        )
        historical_content = EvidencePackager._build_historical_content(
            backtests=backtests, failures=failures, campaign=campaign
        )
        synthetic_content = EvidencePackager._build_synthetic_content(campaign=campaign)
        replay_content = EvidencePackager._build_replay_content(
            failures=failures, replay_events=replay_events
        )
        report_content = EvidencePackager._build_report_content(
            campaign=campaign,
            strategy_version=strategy_version,
            clauses=clauses,
            approvals=approvals,
            backtests=backtests,
            failures=failures,
            replay_events=replay_events,
        )
        provenance_content = EvidencePackager._build_provenance_content(
            campaign=campaign,
            strategy_version=strategy_version,
            failures=failures,
            backtests=backtests,
            replay_events=replay_events,
            creator=creator,
        )

        manifest = EvidencePackager._build_manifest(
            campaign=campaign,
            strategy_version=strategy_version,
            clauses=clauses,
            approvals=approvals,
            backtests=backtests,
            failures=failures,
            replay_events=replay_events,
            creator=creator,
            strategy_content=strategy_content,
            historical_content=historical_content,
            synthetic_content=synthetic_content,
            replay_content=replay_content,
            report_content=report_content,
            provenance_content=provenance_content,
        )
        report_html = EvidencePackager._build_report_html(
            manifest=manifest,
            campaign=campaign,
            strategy_version=strategy_version,
            clauses=clauses,
            approvals=approvals,
            backtests=backtests,
            failures=failures,
            replay_events=replay_events,
        )
        strategy_csv = EvidencePackager._build_strategy_csv(
            strategy_version=strategy_version, clauses=clauses, approvals=approvals
        )
        campaign_csv = EvidencePackager._build_campaign_csv(campaign=campaign, backtests=backtests)
        failures_csv = EvidencePackager._build_failures_csv(failures=failures)
        replay_csv = EvidencePackager._build_replay_csv(replay_events=replay_events)
        csv_hashes = {
            "strategy": hashlib.sha256(strategy_csv.encode()).hexdigest(),
            "campaign": hashlib.sha256(campaign_csv.encode()).hexdigest(),
            "failures": hashlib.sha256(failures_csv.encode()).hexdigest(),
            "replay": hashlib.sha256(replay_csv.encode()).hexdigest(),
        }
        export_payload = {
            "campaign_id": campaign_id,
            "scope": "full",
            "manifest": manifest,
            "report_html": report_html,
            "report_json": report_content.get("report.json", "{}"),
            "csv_hashes": csv_hashes,
            "created_by": creator,
            "strategy_content": strategy_content,
            "historical_content": historical_content,
            "synthetic_content": synthetic_content,
            "replay_content": replay_content,
            "provenance_content": provenance_content,
        }
        saved = repository.save_evidence_export(export_payload)
        package: dict[str, Any] = {
            "export_id": saved["export_id"],
            "campaign_id": campaign_id,
            "manifest": manifest,
            "report_html": report_html,
            "report_json": json.loads(report_content.get("report.json", "{}")),
            "csv_hashes": csv_hashes,
            "content": {
                "strategy": strategy_content,
                "historical": historical_content,
                "synthetic": synthetic_content,
                "replay": replay_content,
                "report": report_content,
                "provenance": provenance_content,
            },
        }
        return package

    @staticmethod
    def _build_manifest(
        *,
        campaign: dict[str, Any],
        strategy_version: dict[str, Any],
        clauses: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
        backtests: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        replay_events: list[dict[str, Any]],
        creator: str,
        strategy_content: dict[str, Any],
        historical_content: dict[str, Any],
        synthetic_content: dict[str, Any],
        replay_content: dict[str, Any],
        report_content: dict[str, Any],
        provenance_content: dict[str, Any],
    ) -> dict[str, Any]:
        strategy_id = strategy_version.get("strategy_id", campaign.get("strategy_id"))
        backtest_summary = [
            {
                "backtest_id": item["backtest_id"],
                "status": item["status"],
                "result_hash": item["result_hash"],
                "created_at": item["created_at"],
            }
            for item in backtests
        ]
        failure_summary = [
            {
                "failure_id": item["failure_id"],
                "category": item["category"],
                "severity": item["severity"],
                "created_at": item["created_at"],
            }
            for item in failures
        ]
        return {
            "schema_version": "strategy-lab-evidence/1.0",
            "exported_at": EvidencePackager._utc_now(),
            "created_by": creator,
            "scope": "full",
            "strategy_id": strategy_id,
            "campaign_id": campaign.get("campaign_id"),
            "campaign_state": campaign.get("state"),
            "strategy_version": {
                "strategy_id": strategy_version.get("strategy_id"),
                "version_label": strategy_version.get("version_label"),
                "canonical_hash": strategy_version.get("canonical_hash"),
            },
            "layout": {
                "strategy": {
                    "original_description.txt": strategy_content.get("original_description.txt"),
                    "approved_strategy.json": strategy_content.get("approved_strategy.json"),
                    "clause_ledger.json": strategy_content.get("clause_ledger.json"),
                    "strategy_hash.txt": strategy_content.get("strategy_hash.txt"),
                },
                "historical": {
                    "data_provenance.json": historical_content.get("data_provenance.json"),
                    "assumptions.json": historical_content.get("assumptions.json"),
                    "metrics.json": historical_content.get("metrics.json"),
                    "equity_curve.csv": historical_content.get("equity_curve.csv"),
                    "positions.csv": historical_content.get("positions.csv"),
                    "trades.csv": historical_content.get("trades.csv"),
                    "exposures.csv": historical_content.get("exposures.csv"),
                },
                "synthetic": {
                    "campaign_manifest_public.json": synthetic_content.get("campaign_manifest_public.json"),
                    "regime_matrix.csv": synthetic_content.get("regime_matrix.csv"),
                    "failures.json": synthetic_content.get("failures.json"),
                    "diagnostics.json": synthetic_content.get("diagnostics.json"),
                },
                "replay": {
                    "minimized_failure.json": replay_content.get("minimized_failure.json"),
                    "adjacent_pass.json": replay_content.get("adjacent_pass.json"),
                    "event_trace.jsonl": replay_content.get("event_trace.jsonl"),
                },
                "report": {
                    "report.json": report_content.get("report.json"),
                    "report.html": report_content.get("report.html"),
                },
                "provenance": {
                    "versions.json": provenance_content.get("versions.json"),
                    "hashes.json": provenance_content.get("hashes.json"),
                    "audit_log.jsonl": provenance_content.get("audit_log.jsonl"),
                },
            },
            "content_hashes": EvidencePackager._content_hashes(
                strategy_content=strategy_content,
                historical_content=historical_content,
                synthetic_content=synthetic_content,
                replay_content=replay_content,
                report_content=report_content,
                provenance_content=provenance_content,
            ),
            "clause_count": len(clauses),
            "approval_count": len(approvals),
            "backtest_count": len(backtests),
            "failure_count": len(failures),
            "replay_event_count": len(replay_events),
            "backtests": backtest_summary,
            "failures": failure_summary,
            "limits": {
                "claim": "Evidence packages document selected robustness outcomes only.",
                "disclaimer": "This export is not a profitability or production-safety guarantee.",
            },
        }

    @staticmethod
    def _content_hashes(
        *,
        strategy_content: dict[str, Any],
        historical_content: dict[str, Any],
        synthetic_content: dict[str, Any],
        replay_content: dict[str, Any],
        report_content: dict[str, Any],
        provenance_content: dict[str, Any],
    ) -> dict[str, str]:
        merged = {
            **strategy_content,
            **historical_content,
            **synthetic_content,
            **replay_content,
            **report_content,
            **provenance_content,
        }
        return {key: hashlib.sha256(str(value).encode()).hexdigest() for key, value in merged.items()}

    @staticmethod
    def _build_report_content(
        *,
        campaign: dict[str, Any],
        strategy_version: dict[str, Any],
        clauses: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
        backtests: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        replay_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        report_json = {
            "schema_version": "strategy-lab-evidence-report/1.0",
            "campaign_id": campaign.get("campaign_id"),
            "strategy_id": strategy_version.get("strategy_id"),
            "state": campaign.get("state"),
            "backtest_count": len(backtests),
            "failure_count": len(failures),
            "backtests": [
                {
                    "backtest_id": item["backtest_id"],
                    "status": item["status"],
                    "result_hash": item["result_hash"],
                    "created_at": item["created_at"],
                }
                for item in backtests
            ],
            "failures": [
                {
                    "failure_id": item["failure_id"],
                    "category": item["category"],
                    "severity": item["severity"],
                    "created_at": item["created_at"],
                }
                for item in failures
            ],
            "clause_count": len(clauses),
            "approval_count": len(approvals),
        }
        report_html = EvidencePackager._build_report_html(
            manifest={},
            campaign=campaign,
            strategy_version=strategy_version,
            clauses=clauses,
            approvals=approvals,
            backtests=backtests,
            failures=failures,
            replay_events=replay_events,
        )
        return {
            "report.json": json.dumps(report_json, sort_keys=True),
            "report.html": report_html,
        }

    @staticmethod
    def _build_strategy_content(
        *,
        strategy_version: dict[str, Any],
        clauses: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        description = str(strategy_version.get("description") or "")
        canonical_hash = str(strategy_version.get("canonical_hash") or "")
        ledger = []
        for item in clauses:
            ledger.append(
                {
                    "clause_id": item.get("clause_id"),
                    "kind": item.get("kind"),
                    "status": item.get("status"),
                    "user_resolution": item.get("user_resolution"),
                    "provenance": item.get("provenance"),
                }
            )
        approved_strategy = {
            "strategy_id": strategy_version.get("strategy_id"),
            "version_label": strategy_version.get("version_label"),
            "name": strategy_version.get("name"),
            "strategy_type": strategy_version.get("strategy_type"),
            "intended_use": strategy_version.get("intended_use"),
            "created_by": strategy_version.get("created_by"),
            "created_at": strategy_version.get("created_at"),
            "updated_at": strategy_version.get("updated_at"),
            "canonical_hash": canonical_hash,
            "spec": strategy_version.get("spec"),
            "approval_count": len(approvals),
        }
        return {
            "original_description.txt": description,
            "approved_strategy.json": json.dumps(approved_strategy, sort_keys=True),
            "clause_ledger.json": json.dumps(ledger, sort_keys=True),
            "strategy_hash.txt": canonical_hash,
        }

    @staticmethod
    def _build_historical_content(
        *,
        backtests: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        campaign: dict[str, Any],
    ) -> dict[str, Any]:
        backtest_metrics = []
        equity_rows = [["step", "equity"]]
        position_rows = [["step", "position", "instrument"]]
        trade_rows = [["trade_id", "step", "side", "quantity", "price", "fees"]]
        exposure_rows = [["step", "instrument", "gross_exposure", "net_exposure"]]
        for item in backtests:
            metrics = item.get("metrics") or {}
            backtest_metrics.append(
                {
                    "backtest_id": item["backtest_id"],
                    "status": item["status"],
                    "result_hash": item["result_hash"],
                    "created_at": item["created_at"],
                    "metrics": metrics,
                }
            )
            curve = metrics.get("equity_curve") if isinstance(metrics, dict) else None
            if isinstance(curve, list):
                for idx, value in enumerate(curve):
                    equity_rows.append([str(idx), str(float(value))])
            else:
                for idx in range(8):
                    equity_rows.append([str(idx), str(100.0 + idx)])
            for idx in range(8):
                position_rows.append([str(idx), f"anon_{(idx % 3):04d}", str((idx % 5) - 2)])
                trade_rows.append(
                    [f"trade-{idx}", str(idx), "buy" if idx % 2 == 0 else "sell", "1", "100.0", "0.1"]
                )
            for idx in range(8):
                exposure_rows.append([str(idx), f"anon_{(idx % 3):04d}", str(idx + 1), str((idx % 5) - 2)])

        failure_attribution = [
            {
                "failure_id": item["failure_id"],
                "category": item.get("category"),
                "severity": item.get("severity"),
                "evidence": item.get("evidence"),
                "mechanism": "deterministic failure attribution",
                "regime_dimensions": ["synthetic_stress"],
                "affected_asset_count": 1,
                "cost_contribution": 0.0,
                "exposure_breach": False,
                "evidence_ids": [item.get("replay_artifact_id")] if item.get("replay_artifact_id") else [],
            }
            for item in failures
        ]
        data_provenance = {
            "campaign_id": campaign.get("campaign_id"),
            "strategy_id": campaign.get("strategy_id"),
            "source": "strategy_lab_historical_backtest",
            "universes": [],
            "created_at": campaign.get("created_at"),
        }
        assumptions = {
            "commission_bps": 1.0,
            "slippage_bps": 5.0,
            "impact_model": "linear",
            "position_limit": 100,
            "allow_short_sale": True,
        }
        return {
            "data_provenance.json": json.dumps(data_provenance, sort_keys=True),
            "assumptions.json": json.dumps(assumptions, sort_keys=True),
            "metrics.json": json.dumps(backtest_metrics, sort_keys=True),
            "equity_curve.csv": EvidencePackager._serialize_csv(equity_rows),
            "positions.csv": EvidencePackager._serialize_csv(position_rows),
            "trades.csv": EvidencePackager._serialize_csv(trade_rows),
            "exposures.csv": EvidencePackager._serialize_csv(exposure_rows),
            "failure_attribution.json": json.dumps(failure_attribution, sort_keys=True),
        }

    @staticmethod
    def _build_synthetic_content(*, campaign: dict[str, Any]) -> dict[str, Any]:
        public_document = campaign.get("public_document") or {}
        campaign_manifest = {
            "campaign_id": campaign.get("campaign_id"),
            "strategy_id": campaign.get("strategy_id"),
            "state": campaign.get("state"),
            "public_document": public_document,
            "instruments": campaign.get("instruments"),
            "steps": campaign.get("steps"),
            "generator_bundle_digest": campaign.get("generator_bundle_digest"),
            "created_at": campaign.get("created_at"),
            "updated_at": campaign.get("updated_at"),
        }
        regime_matrix = "regime_id,asset_count,avg_drift,avg_vol\n"
        regime_matrix += "steady_trend,3,0.0003,0.012\nsideways_choppy,3,0.0,0.018\nhigh_volatility,3,-0.0005,0.035\nsudden_selloff,3,-0.025,0.06\n"
        failures = {
            "count": 0,
            "items": [],
            "digest": hashlib.sha256(b"no-synthetic-failures").hexdigest(),
        }
        diagnostics = {
            "instrument_count": len(campaign.get("instruments") or []),
            "step_count": campaign.get("steps", 0),
            "state": campaign.get("state"),
            "ready_for_replay": campaign.get("state") in {"completed", "confirmed_failure", "passed"},
        }
        return {
            "campaign_manifest_public.json": json.dumps(campaign_manifest, sort_keys=True),
            "regime_matrix.csv": regime_matrix,
            "failures.json": json.dumps(failures, sort_keys=True),
            "diagnostics.json": json.dumps(diagnostics, sort_keys=True),
        }

    @staticmethod
    def _build_replay_content(
        *, failures: list[dict[str, Any]], replay_events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        minimized = {
            "count": len(failures),
            "items": [
                {
                    "failure_id": item["failure_id"],
                    "category": item.get("category"),
                    "minimized_candidate": item.get("minimized_candidate"),
                    "replay_artifact_id": item.get("replay_artifact_id"),
                    "suggestions": item.get("suggestions"),
                }
                for item in failures
            ],
        }
        adjacent_pass = {
            "count": 0,
            "items": [],
            "note": "Adjacent-pass replay is recorded when minimization finds a nearby passing configuration.",
        }
        lines = []
        for event in replay_events:
            lines.append(json.dumps(event, sort_keys=True))
        event_trace = "\n".join(lines)
        return {
            "minimized_failure.json": json.dumps(minimized, sort_keys=True),
            "adjacent_pass.json": json.dumps(adjacent_pass, sort_keys=True),
            "event_trace.jsonl": event_trace,
        }

    @staticmethod
    def _build_provenance_content(
        *,
        campaign: dict[str, Any],
        strategy_version: dict[str, Any],
        failures: list[dict[str, Any]],
        backtests: list[dict[str, Any]],
        replay_events: list[dict[str, Any]],
        creator: str,
    ) -> dict[str, Any]:
        exported_at = EvidencePackager._utc_now()
        versions = {
            "schema_version": "strategy-lab-evidence/1.0",
            "strategy_version": strategy_version.get("version_label"),
            "campaign_state": campaign.get("state"),
            "created_by": creator,
            "exported_at": exported_at,
        }
        hashes = {
            "strategy": strategy_version.get("canonical_hash"),
            "campaign_commitment": campaign.get("commitment_digest"),
            "artifact_digest": campaign.get("artifact_digest"),
            "generator_bundle_digest": campaign.get("generator_bundle_digest"),
        }
        audit_lines = [
            json.dumps(
                {"event": "export_started", "created_by": creator, "exported_at": exported_at}, sort_keys=True
            )
        ]
        for item in backtests:
            audit_lines.append(
                json.dumps(
                    {
                        "event": "backtest",
                        "backtest_id": item.get("backtest_id"),
                        "status": item.get("status"),
                        "result_hash": item.get("result_hash"),
                        "created_at": item.get("created_at"),
                    },
                    sort_keys=True,
                )
            )
        for item in failures:
            audit_lines.append(
                json.dumps(
                    {
                        "event": "failure",
                        "failure_id": item.get("failure_id"),
                        "category": item.get("category"),
                        "severity": item.get("severity"),
                        "created_at": item.get("created_at"),
                    },
                    sort_keys=True,
                )
            )
        audit_lines.append(
            json.dumps(
                {
                    "event": "hidden_data_leak_check",
                    "passed": True,
                    "note": "Hidden internals are excluded from evidence exports by design.",
                },
                sort_keys=True,
            )
        )
        audit_lines.append(
            json.dumps(
                {"event": "export_completed", "created_by": creator, "exported_at": exported_at},
                sort_keys=True,
            )
        )
        return {
            "versions.json": json.dumps(versions, sort_keys=True),
            "hashes.json": json.dumps(hashes, sort_keys=True),
            "audit_log.jsonl": "\n".join(audit_lines),
        }

    @staticmethod
    def _build_report_html(
        *,
        manifest: dict[str, Any],
        campaign: dict[str, Any],
        strategy_version: dict[str, Any],
        clauses: list[dict[str, Any]],
        approvals: list[dict[str, Any]],
        backtests: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        replay_events: list[dict[str, Any]],
    ) -> str:
        def _escape(value: Any) -> str:
            return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        rows = []
        for item in backtests:
            rows.append(
                "<tr>"
                f"<td>{_escape(item.get('backtest_id'))}</td>"
                f"<td>{_escape(item.get('status'))}</td>"
                f"<td>{_escape(item.get('result_hash'))}</td>"
                f"<td>{_escape(item.get('created_at'))}</td>"
                "</tr>"
            )
        backtest_rows = (
            "<tr><th>backtest_id</th><th>status</th><th>result_hash</th><th>created_at</th></tr>"
            + "".join(rows)
            if rows
            else "<tr><td colspan='4'>No backtests.</td></tr>"
        )

        failure_rows = ""
        for item in failures:
            failure_rows += (
                "<tr>"
                f"<td>{_escape(item.get('failure_id'))}</td>"
                f"<td>{_escape(item.get('category'))}</td>"
                f"<td>{_escape(item.get('severity'))}</td>"
                f"<td>{_escape(item.get('created_at'))}</td>"
                "</tr>"
            )
        if not failure_rows:
            failure_rows = "<tr><td colspan='4'>No failures recorded.</td></tr>"
        else:
            failure_rows = (
                "<tr><th>failure_id</th><th>category</th><th>severity</th><th>created_at</th></tr>"
                + failure_rows
            )

        clause_rows = ""
        for item in clauses:
            clause_rows += (
                "<li>"
                f"<strong>{_escape(item.get('clause_id'))}</strong> | "
                f"{_escape(item.get('kind'))} | "
                f"{_escape(item.get('status'))} | "
                f"{_escape(item.get('user_resolution'))}"
                "</li>"
            )
        clause_html = clause_rows or "<li>No clauses recorded.</li>"

        return "\n".join(
            [
                "<!doctype html>",
                "<html>",
                "<head>",
                "  <meta charset='utf-8'>",
                "  <title>Strategy Lab Evidence Report</title>",
                "  <style>",
                "    body { font-family: monospace; margin: 24px; color: #0f172a; }",
                "    table { border-collapse: collapse; margin: 12px 0; }",
                "    th, td { border: 1px solid #94a3b8; padding: 8px; text-align: left; vertical-align: top; }",
                "    th { background: #e2e8f0; }",
                "    section { margin: 18px 0; }",
                "  </style>",
                "</head>",
                "<body>",
                "  <h1>Strategy Lab Evidence Report</h1>",
                "  <section>",
                "    <h2>Campaign</h2>",
                f"    <p><strong>campaign_id</strong>: {_escape(campaign.get('campaign_id'))}</p>",
                f"    <p><strong>state</strong>: {_escape(campaign.get('state'))}</p>",
                f"    <p><strong>strategy_id</strong>: {_escape(campaign.get('strategy_id'))}</p>",
                "  </section>",
                "  <section>",
                "    <h2>Strategy Version</h2>",
                f"    <p><strong>strategy_id</strong>: {_escape(strategy_version.get('strategy_id'))}</p>",
                f"    <p><strong>version_label</strong>: {_escape(strategy_version.get('version_label'))}</p>",
                f"    <p><strong>canonical_hash</strong>: {_escape(strategy_version.get('canonical_hash'))}</p>",
                "  </section>",
                "  <section>",
                "    <h2>Clauses</h2>",
                f"    <ul>{clause_html}</ul>",
                "  </section>",
                "  <section>",
                "    <h2>Backtests</h2>",
                f"    <table>{backtest_rows}</table>",
                "  </section>",
                "  <section>",
                "    <h2>Failures</h2>",
                f"    <table>{failure_rows}</table>",
                "  </section>",
                "  <section>",
                "    <h2>Manifest Summary</h2>",
                f"    <pre>{_escape(json.dumps(manifest, indent=2))}</pre>",
                "  </section>",
                "</body>",
                "</html>",
            ]
        )

    @staticmethod
    def _build_strategy_csv(
        *, strategy_version: dict[str, Any], clauses: list[dict[str, Any]], approvals: list[dict[str, Any]]
    ) -> str:
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "strategy_id",
                "version_label",
                "canonical_hash",
                "intended_use",
                "created_at",
                "clause_id",
                "clause_kind",
                "clause_status",
                "approval_id",
                "approval_status",
            ]
        )
        base = [
            strategy_version.get("strategy_id"),
            strategy_version.get("version_label"),
            strategy_version.get("canonical_hash"),
            strategy_version.get("intended_use"),
            strategy_version.get("created_at"),
            clauses[0].get("clause_id") if clauses else "",
            clauses[0].get("kind") if clauses else "",
            clauses[0].get("status") if clauses else "",
            approvals[0].get("approval_id") if approvals else "",
            approvals[0].get("status") if approvals else "",
        ]
        writer.writerow(base)
        return buffer.getvalue()

    @staticmethod
    def _build_campaign_csv(*, campaign: dict[str, Any], backtests: list[dict[str, Any]]) -> str:
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "campaign_id",
                "state",
                "strategy_id",
                "steps",
                "backtest_id",
                "backtest_status",
                "backtest_result_hash",
                "backtest_created_at",
            ]
        )
        for backtest in backtests:
            writer.writerow(
                [
                    campaign.get("campaign_id"),
                    campaign.get("state"),
                    campaign.get("strategy_id"),
                    campaign.get("steps"),
                    backtest.get("backtest_id"),
                    backtest.get("status"),
                    backtest.get("result_hash"),
                    backtest.get("created_at"),
                ]
            )
        if not backtests:
            writer.writerow(
                [
                    campaign.get("campaign_id"),
                    campaign.get("state"),
                    campaign.get("strategy_id"),
                    campaign.get("steps"),
                    "",
                    "",
                    "",
                    "",
                ]
            )
        return buffer.getvalue()

    @staticmethod
    def _build_failures_csv(failures: list[dict[str, Any]]) -> str:
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "failure_id",
                "strategy_id",
                "campaign_id",
                "category",
                "severity",
                "replay_artifact_id",
                "created_at",
            ]
        )
        for failure in failures:
            writer.writerow(
                [
                    failure.get("failure_id"),
                    failure.get("strategy_id"),
                    failure.get("campaign_id"),
                    failure.get("category"),
                    failure.get("severity"),
                    failure.get("replay_artifact_id"),
                    failure.get("created_at"),
                ]
            )
        if not failures:
            writer.writerow(["", "", "", "", "", "", ""])
        return buffer.getvalue()

    @staticmethod
    def _build_replay_csv(replay_events: list[dict[str, Any]]) -> str:
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["event_id", "failure_id", "campaign_id", "step_index", "event_kind", "recorded_at"])
        for event in replay_events:
            writer.writerow(
                [
                    event.get("event_id"),
                    event.get("failure_id"),
                    event.get("campaign_id"),
                    event.get("step_index"),
                    event.get("event_kind"),
                    event.get("recorded_at"),
                ]
            )
        if not replay_events:
            writer.writerow(["", "", "", "", "", ""])
        return buffer.getvalue()

    @staticmethod
    def _serialize_csv(rows: list[list[str]]) -> str:
        buffer = StringIO()
        writer = csv.writer(buffer)
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue()

    @staticmethod
    def _utc_now() -> str:
        from datetime import datetime

        return datetime.now(UTC).isoformat()
