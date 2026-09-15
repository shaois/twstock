from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PagesRefreshTriggerTests(unittest.TestCase):
    def test_all_local_scripts_are_published_and_trigger_deployment(self):
        import re
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="([^"?]+)(?:\?[^\"]*)?"', html)
        self.assertIn("trade_plan.js", scripts)
        for script in scripts:
            if "://" in script:
                continue
            self.assertTrue((ROOT / script).is_file())
            self.assertIn(f'      - "{script}"', workflow)
            self.assertIn(f"cp {script} _site/{script}", workflow)

    def test_successful_cache_refresh_triggers_pages_deployment(self):
        workflow = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_run:", workflow)
        self.assertIn('workflows: ["Daily FinMind Cache Refresh"]', workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)


if __name__ == "__main__":
    unittest.main()
