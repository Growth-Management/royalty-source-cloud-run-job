from __future__ import annotations

import unittest
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "deploy-production-publish.yml"
)


class DeployProductionPublishWorkflowTest(unittest.TestCase):
    def test_env_var_delimiter_does_not_collide_with_approval_email(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertNotIn('"^@^', workflow)
        self.assertIn(
            '"^~^JOB_TARGET_MONTH=${TARGET_MONTH}'
            '~PRODUCTION_PUBLISH_APPLY=${APPLY}'
            '~DELETE_CANDIDATE_APPROVAL_JSON=${APPROVAL_JSON}"',
            workflow,
        )
        self.assertIn(
            '"^~^PRODUCTION_PUBLISH_APPLY=false'
            '~DELETE_CANDIDATE_APPROVAL_JSON="',
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
