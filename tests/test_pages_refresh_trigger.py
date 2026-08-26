from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PagesRefreshTriggerTests(unittest.TestCase):
    def test_successful_cache_refresh_triggers_pages_deployment(self):
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_run:", workflow)
        self.assertIn('workflows: ["Daily FinMind Cache Refresh"]', workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)


if __name__ == "__main__":
    unittest.main()
