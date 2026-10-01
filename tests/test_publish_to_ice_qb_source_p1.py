"""Publisher integration tests with BigQuery replaced by mocks (no GCP access)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

from app.source_diff_worker import DeleteCandidateApproval, DiffSummary

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "publish_to_ice_qb_source_p1.py"

_spec = importlib.util.spec_from_file_location("publish_to_ice_qb_source_p1", SCRIPT_PATH)
publisher = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = publisher
_spec.loader.exec_module(publisher)

MATCHED = DiffSummary(3, 3, 0, 3, 0, 0)
INSERT_ONLY = DiffSummary(5, 3, 1, 3, 2, 0)
WITH_DELETE_CANDIDATE = DiffSummary(3, 4, 1, 3, 0, 1)


def _field(name: str) -> mock.Mock:
    # mock.Mock(name=...) sets the repr name, so assign the attribute explicitly.
    field = mock.Mock()
    field.name = name
    return field


class ScriptEntryPointTest(unittest.TestCase):
    def test_script_mode_can_import_app_package(self) -> None:
        # Cloud Run runs `python scripts/publish_to_ice_qb_source_p1.py` from /app.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            cwd=str(REPO_ROOT / "scripts"),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--target-month", result.stdout)


class DecidePublishStatusTest(unittest.TestCase):
    def test_all_matched_is_already_matched_even_with_apply(self) -> None:
        for apply in (False, True):
            self.assertEqual(
                publisher.decide_publish_status({"sales": MATCHED, "store": MATCHED}, apply),
                "ALREADY_MATCHED",
            )

    def test_dry_run_with_delete_candidates_completes(self) -> None:
        self.assertEqual(
            publisher.decide_publish_status({"sales": WITH_DELETE_CANDIDATE}, False),
            "DRY_RUN_MISMATCH",
        )

    def test_apply_with_insert_only_proceeds(self) -> None:
        self.assertEqual(
            publisher.decide_publish_status({"sales": INSERT_ONLY, "pod": MATCHED}, True),
            "APPLY",
        )

    def test_apply_with_delete_candidates_is_blocked(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "DELETE_CANDIDATE"):
            publisher.decide_publish_status({"sales": INSERT_ONLY, "pod": WITH_DELETE_CANDIDATE}, True)

    def test_apply_with_exact_reviewed_delete_candidates_proceeds(self) -> None:
        approval = DeleteCandidateApproval(
            target_month="202608",
            approved_by="reviewer@example.com",
            approved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            validation_run_id="999",
            delete_candidate_rows={"pod": 1},
        )
        self.assertEqual(
            publisher.decide_publish_status(
                {"sales": INSERT_ONLY, "pod": WITH_DELETE_CANDIDATE},
                True,
                target_month="202608",
                validation_run_id="999",
                approval=approval,
            ),
            "APPLY",
        )

    def test_apply_with_approval_for_different_validation_run_is_blocked(self) -> None:
        approval = DeleteCandidateApproval(
            target_month="202608",
            approved_by="reviewer@example.com",
            approved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            validation_run_id="998",
            delete_candidate_rows={"pod": 1},
        )
        with self.assertRaisesRegex(RuntimeError, "validation_run_id"):
            publisher.decide_publish_status(
                {"pod": WITH_DELETE_CANDIDATE},
                True,
                target_month="202608",
                validation_run_id="999",
                approval=approval,
            )


class ApprovalParsingTest(unittest.TestCase):
    def test_parses_reviewed_approval(self) -> None:
        approval = publisher.parse_delete_candidate_approval(
            '{"target_month":"202608","approved_by":"reviewer@example.com",'
            '"approved_at":"2026-10-01T10:00:00Z","validation_run_id":"999",'
            '"delete_candidate_rows":{"sales":258,"store":1397,"pod":0}}'
        )
        self.assertEqual(approval.target_month, "202608")
        self.assertEqual(approval.delete_candidate_rows["store"], 1397)
        self.assertIsNotNone(approval.approved_at.tzinfo)

    def test_rejects_unknown_table(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown tables"):
            publisher.parse_delete_candidate_approval(
                '{"target_month":"202608","approved_by":"reviewer@example.com",'
                '"approved_at":"2026-10-01T10:00:00Z","validation_run_id":"999",'
                '"delete_candidate_rows":{"authors":31}}'
            )

    def test_rejects_non_integer_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            publisher.parse_delete_candidate_approval(
                '{"target_month":"202608","approved_by":"reviewer@example.com",'
                '"approved_at":"2026-10-01T10:00:00Z","validation_run_id":"999",'
                '"delete_candidate_rows":{"sales":1.5}}'
            )


class AuditColumnsTest(unittest.TestCase):
    def test_diff_audit_columns(self) -> None:
        self.assertEqual(
            set(publisher.DIFF_AUDIT_COLUMNS),
            {
                f"{key}_{metric}_rows_before"
                for key in ("sales", "store", "pod")
                for metric in ("unchanged", "insert", "delete_candidate")
            },
        )

    def test_alter_sql_only_for_missing_columns(self) -> None:
        table_id = "ice-qb.royalty_audit.production_publish_log"
        all_optional_columns = set(publisher.DIFF_AUDIT_COLUMNS) | set(publisher.APPROVAL_AUDIT_COLUMNS)
        self.assertIsNone(publisher.build_add_diff_columns_sql(table_id, all_optional_columns))
        existing = all_optional_columns - {"pod_unchanged_rows_before", "sales_insert_rows_before"}
        sql = publisher.build_add_diff_columns_sql(table_id, existing)
        self.assertEqual(sql.count("ALTER TABLE"), 1)
        self.assertEqual(sql.count("ADD COLUMN IF NOT EXISTS"), 2)
        self.assertIn("pod_unchanged_rows_before INT64", sql)
        self.assertIn("sales_insert_rows_before INT64", sql)

    def test_ensure_audit_table_skips_ddl_when_columns_exist(self) -> None:
        client = mock.MagicMock()
        client.get_table.return_value.schema = [
            _field(name)
            for name in (*publisher.DIFF_AUDIT_COLUMNS, *publisher.APPROVAL_AUDIT_COLUMNS)
        ]
        publisher.ensure_audit_table(client, "ice-qb", "royalty_audit", "asia-northeast1")
        issued = [call.args[0] for call in client.query.call_args_list]
        self.assertEqual(len(issued), 1)
        self.assertIn("CREATE TABLE IF NOT EXISTS", issued[0])

    def test_ensure_audit_table_adds_missing_columns_on_existing_table(self) -> None:
        client = mock.MagicMock()
        client.get_table.return_value.schema = [_field("status")]
        publisher.ensure_audit_table(client, "ice-qb", "royalty_audit", "asia-northeast1")
        issued = [call.args[0] for call in client.query.call_args_list]
        self.assertEqual(len(issued), 2)
        self.assertTrue(issued[1].startswith("ALTER TABLE `ice-qb.royalty_audit.production_publish_log`"))
        self.assertEqual(issued[1].count("ADD COLUMN IF NOT EXISTS"), 13)


class StageMonthGuardTest(unittest.TestCase):
    def _client(self, out_of_month_rows: int) -> mock.MagicMock:
        client = mock.MagicMock()
        row = mock.Mock()
        row.out_of_month_rows = out_of_month_rows
        client.query.return_value.result.return_value = iter([row])
        return client

    def test_passes_when_all_rows_in_target_month(self) -> None:
        client = self._client(0)
        publisher.ensure_stage_within_target_month(client, "p.d.stage", "year_month", "202608", "US")
        sql = client.query.call_args.args[0]
        self.assertIn("year_month IS NULL OR year_month != @target_month_int", sql)

    def test_blocks_out_of_month_rows(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outside target month"):
            publisher.ensure_stage_within_target_month(self._client(2), "p.d.stage", "year_month", "202608", "US")


class MainFlowTest(unittest.TestCase):
    """Run main() with every BigQuery touchpoint mocked."""

    def _run_main(
        self,
        *,
        apply: bool,
        before: dict[str, DiffSummary],
        after: dict[str, DiffSummary] | None = None,
        audit_error: Exception | None = None,
        month_guard_error: Exception | None = None,
        approval_json: str = "",
    ) -> tuple[dict, mock.MagicMock, mock.MagicMock, Exception | None]:
        after = after or before
        compare_results = [before[k] for k in ("sales", "store", "pod")] + [
            after[k] for k in ("sales", "store", "pod")
        ]
        audit_payloads: list[dict] = []

        def fake_write_audit(client, table_id, location, payload):
            audit_payloads.append(dict(payload))
            if audit_error:
                raise audit_error

        target_client = mock.MagicMock(name="target_client")
        source_client = mock.MagicMock(name="source_client")
        clients = iter([source_client, target_client])
        argv = ["publish", "--target-month", "202608"] + (["--apply"] if apply else [])
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PRODUCTION_PUBLISH_APPLY", "DELETE_CANDIDATE_APPROVAL_JSON"}
        }
        if approval_json:
            env["DELETE_CANDIDATE_APPROVAL_JSON"] = approval_json

        patches = [
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(publisher.bigquery, "Client", side_effect=lambda **_: next(clients)),
            mock.patch.object(publisher, "ensure_audit_table", return_value="ice-qb.royalty_audit.production_publish_log"),
            mock.patch.object(
                publisher,
                "get_promotion_gate",
                return_value={"run_id": "999", "promoted_at": None, "sales_rows": 3, "store_rows": 3, "pod_rows": 3},
            ),
            mock.patch.object(publisher, "validate_cumulative_snapshot"),
            mock.patch.object(publisher, "query_dataframe", return_value=pd.DataFrame({"a": [1, 2, 3]})),
            mock.patch.object(
                publisher,
                "load_stage_table",
                side_effect=lambda client, df, project, dataset, table, location, suffix: f"{project}.{dataset}._stg_{table}",
            ),
            mock.patch.object(publisher, "compare_stage_to_target", side_effect=compare_results),
            mock.patch.object(publisher, "apply_transaction"),
            mock.patch.object(publisher, "write_audit", side_effect=fake_write_audit),
            mock.patch.object(publisher, "ensure_stage_within_target_month", side_effect=month_guard_error),
        ]
        started = [p.start() for p in patches]
        try:
            error = None
            try:
                publisher.main()
            except Exception as exc:  # noqa: BLE001 - asserted by callers
                error = exc
            apply_transaction = started[9]
        finally:
            for p in reversed(patches):
                p.stop()
        return audit_payloads[-1], apply_transaction, target_client, error

    def _all(self, summary: DiffSummary) -> dict[str, DiffSummary]:
        return {"sales": summary, "store": summary, "pod": summary}

    def test_dry_run_with_delete_candidate_records_counts_without_apply(self) -> None:
        before = {"sales": INSERT_ONLY, "store": WITH_DELETE_CANDIDATE, "pod": MATCHED}
        payload, apply_transaction, target_client, error = self._run_main(apply=False, before=before)
        self.assertIsNone(error)
        apply_transaction.assert_not_called()
        self.assertEqual(payload["status"], "DRY_RUN_MISMATCH")
        self.assertEqual(payload["sales_insert_rows_before"], 2)
        self.assertEqual(payload["sales_unchanged_rows_before"], 3)
        self.assertEqual(payload["store_delete_candidate_rows_before"], 1)
        self.assertEqual(payload["pod_delete_candidate_rows_before"], 0)
        self.assertEqual(target_client.delete_table.call_count, 3)

    def test_apply_with_delete_candidate_is_blocked_and_audited(self) -> None:
        before = {"sales": INSERT_ONLY, "store": WITH_DELETE_CANDIDATE, "pod": MATCHED}
        payload, apply_transaction, target_client, error = self._run_main(apply=True, before=before)
        self.assertIsInstance(error, RuntimeError)
        apply_transaction.assert_not_called()
        self.assertEqual(payload["status"], "FAILED")
        self.assertIn("DELETE_CANDIDATE", payload["error_message"])
        self.assertEqual(payload["store_delete_candidate_rows_before"], 1)
        self.assertEqual(target_client.delete_table.call_count, 3)

    def test_apply_with_reviewed_delete_candidates_runs_and_audits_approval(self) -> None:
        before = {"sales": INSERT_ONLY, "store": WITH_DELETE_CANDIDATE, "pod": MATCHED}
        after = self._all(MATCHED)
        approval_json = (
            '{"target_month":"202608","approved_by":"reviewer@example.com",'
            '"approved_at":"2026-10-01T10:00:00Z","validation_run_id":"999",'
            '"delete_candidate_rows":{"store":1}}'
        )
        payload, apply_transaction, target_client, error = self._run_main(
            apply=True,
            before=before,
            after=after,
            approval_json=approval_json,
        )
        self.assertIsNone(error)
        apply_transaction.assert_called_once()
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual(payload["delete_candidate_approved_by"], "reviewer@example.com")
        self.assertEqual(payload["delete_candidate_approval_validation_run_id"], "999")
        self.assertEqual(target_client.delete_table.call_count, 3)

    def test_apply_insert_only_runs_transaction_and_verifies(self) -> None:
        payload, apply_transaction, _, error = self._run_main(
            apply=True, before=self._all(INSERT_ONLY), after=self._all(DiffSummary(5, 5, 0, 5, 0, 0))
        )
        self.assertIsNone(error)
        apply_transaction.assert_called_once()
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual(payload["target_after_sales_rows"], 5)

    def test_apply_post_verification_failure_is_failed(self) -> None:
        payload, apply_transaction, _, error = self._run_main(
            apply=True, before=self._all(INSERT_ONLY), after=self._all(INSERT_ONLY)
        )
        apply_transaction.assert_called_once()
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("post-publish verification failed", str(error))
        self.assertEqual(payload["status"], "FAILED")

    def test_apply_blocked_when_stage_has_out_of_month_rows(self) -> None:
        payload, apply_transaction, target_client, error = self._run_main(
            apply=True,
            before=self._all(INSERT_ONLY),
            month_guard_error=RuntimeError("stage contains rows outside target month"),
        )
        self.assertIsInstance(error, RuntimeError)
        apply_transaction.assert_not_called()
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(target_client.delete_table.call_count, 3)

    def test_already_matched(self) -> None:
        payload, apply_transaction, _, error = self._run_main(apply=True, before=self._all(MATCHED))
        self.assertIsNone(error)
        apply_transaction.assert_not_called()
        self.assertEqual(payload["status"], "ALREADY_MATCHED")

    def test_stage_cleanup_runs_even_if_audit_write_fails(self) -> None:
        _, _, target_client, error = self._run_main(
            apply=False, before=self._all(INSERT_ONLY), audit_error=RuntimeError("audit down")
        )
        self.assertIsNone(error)
        self.assertEqual(target_client.delete_table.call_count, 3)


if __name__ == "__main__":
    unittest.main()
