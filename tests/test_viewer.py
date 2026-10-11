# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contracts for the offline runs viewer: loading, judging joins, HTML safety."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.viewer.load import load
from harness2.viewer.render import render

SUMMARY = {
    "task_id": "corp/task1",
    "domain": "corp",
    "k": 1,
    "status": "ok",
    "base": [
        {
            "label": "base1",
            "run_id": "demo-oc-agg1/corp/task1/base1",
            "run_dir": "EXEC/base1",
        }
    ],
    "modes": {
        "agg": {
            "mode": "agg",
            "ok": True,
            "finals": ["agg1"],
            "stages": [
                {
                    "label": "inv1",
                    "dim": "INV",
                    "fell_back": False,
                    "kept_base": False,
                    "notes": "tighten the ledger rule",
                }
            ],
        }
    },
}

SELECTION = {
    "task_id": "corp/task1",
    "mode": "agg",
    "chosen_label": "agg1",
    "chosen_is_base": False,
    "why": "cleaner deliverable <b>and</b> correct total",
    "ok": True,
    "fallback": False,
    "error": "",
    "experience": {
        "cue": "when totals disagree",
        "lesson": "sum both signs",
        "evidence": "probe contrast",
        "confidence": "high",
    },
}

JUDGED = [
    {
        "task_id": "corp/task1",
        "mode": "agg",
        "k": 1,
        "label": "agg1",
        "graded": True,
        "frac": 0.75,
        "all_pass": False,
        "n_passed": 3,
        "n_criteria": 4,
    }
]

BASE_JUDGED = [
    {
        "task_id": "corp/task1",
        "mode": "agg",
        "k": 1,
        "label": "base1",
        "graded": True,
        "frac": 0.5,
        "all_pass": False,
        "n_passed": 2,
        "n_criteria": 4,
    }
]


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        exec_root = self.root / "exec" / "demo-oc-agg1" / "corp" / "task1"
        for label in ("base1", "inv1", "agg1"):
            d = exec_root / label
            d.mkdir(parents=True)
            (d / "answer.txt").write_text(f"Total: {label}\n", encoding="utf-8")
            (d / "trajectory.md").write_text(f"[ACTION] run {label}\n", encoding="utf-8")
        work = self.root / "runs" / "demo-oc-agg1" / "corp" / "task1"
        work.mkdir(parents=True)
        (work / "summary.json").write_text(
            json.dumps(SUMMARY).replace("EXEC", str(exec_root)), encoding="utf-8"
        )
        sel = work / "selection" / "agg1"
        sel.mkdir(parents=True)
        (sel / "selection.json").write_text(json.dumps(SELECTION), encoding="utf-8")
        # judged rows sit at the namespace level, as the judge CLI writes them
        ns_dir = self.root / "runs" / "demo-oc-agg1"
        (ns_dir / "judged.json").write_text(json.dumps(JUDGED), encoding="utf-8")
        (ns_dir / "base_judged.json").write_text(json.dumps(BASE_JUDGED), encoding="utf-8")

    def test_load_joins_everything(self):
        tree = load(self.root)
        self.assertEqual(len(tree.tasks), 1)
        task = tree.tasks[0]
        self.assertEqual(task.namespace, "demo-oc-agg1")
        self.assertEqual({r.label for r in task.rollouts}, {"base1", "inv1", "agg1"})
        self.assertTrue(all(r.trajectory for r in task.rollouts))
        self.assertEqual(task.selections["agg"]["chosen_label"], "agg1")
        self.assertEqual(task.judged["agg"]["frac"], 0.75)
        self.assertEqual(task.judged_base["agg"]["frac"], 0.5)
        self.assertTrue(any("sum both signs" in e for e in task.experience))

    def test_render_reports_gains_and_escapes(self):
        html = render(load(self.root))
        self.assertIn("corp/task1", html)
        self.assertIn("75.0%", html)  # refined
        self.assertIn("50.0%", html)  # base
        self.assertIn("+25.0", html)  # the gain
        self.assertIn("cleaner deliverable &lt;b&gt;and&lt;/b&gt; correct total", html)
        self.assertIn("sum both signs", html)
        self.assertIn("<style>", html)
        # no raw tag injection from content fields
        self.assertNotIn("<b>and</b>", html)

    def test_render_charts_when_judged(self):
        html = render(load(self.root))
        self.assertIn("<svg", html)
        self.assertIn('aria-label="base vs refined scores"', html)
        self.assertIn('aria-label="mean gain per domain"', html)
        self.assertIn("+25", html)  # the per-task delta label
        demo = render(load(self.root))  # judged present here, charts must appear
        self.assertGreaterEqual(demo.count("<svg"), 2)


if __name__ == "__main__":
    unittest.main()
