from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import diagnose_production_diff as diagnostic  # noqa: E402
from app.source_diff_worker import DiffSummary  # noqa: E402


class DiagnoseProductionDiffImportTest(unittest.TestCase):
    def test_uses_current_diff_summary_type(self) -> None:
        self.assertIs(diagnostic.DiffSummary, DiffSummary)


if __name__ == "__main__":
    unittest.main()
