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

"""Contracts for the dsh substrate: surface, staging composition, event rendering, registry."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness2.core import tree as HT
from harness2.substrate import registry
from harness2.substrate.codex import CodexSubstrate
from harness2.substrate.dsh import (
    DSH_SURFACE,
    DshSubstrate,
    compose_agents_md,
    final_message,
    render_events,
)
from harness2.substrate.opencode import OpencodeSubstrate


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


TREE_FILES = {
    "systemprompt.md": "# Operator instructions\nDo the work carefully.",
    "guardrails.md": "Stay inside the task folder.",
    "LongTermMEMORY.md": "Client prefers bullet lists.",
    "tool_descriptions/tool_guidance.md": "Prefer the grep tool over shelling out.",
    "skills/pdf-extract/SKILL.md": (
        "---\nname: pdf-extract\ndescription: extract tables from PDF deliverables\n---\n"
        "# pdf-extract\nUse pdfplumber."
    ),
    "scripts/tally.py": "print('tally')\n",
}


class SurfaceTests(unittest.TestCase):
    def test_editable_paths(self):
        for rel in (
            "systemprompt.md",
            "guardrails.md",
            "LongTermMEMORY.md",
            "tool_descriptions/tool_guidance.md",
            "skills/pdf-extract/SKILL.md",
            "skills/pdf-extract/scripts/run.py",
            "scripts/tally.py",
        ):
            self.assertTrue(HT.is_allowed(DSH_SURFACE, rel), rel)
        for rel in ("plugin/x.ts", ".codex/hooks.json", "other.md", "../escape.md"):
            self.assertFalse(HT.is_allowed(DSH_SURFACE, rel), rel)

    def test_validate_minimal_tree(self):
        with tempfile.TemporaryDirectory() as td:
            tree = HT.read_tree(DSH_SURFACE, _write_tree(Path(td), TREE_FILES))
            HT.autotrigger(DSH_SURFACE, tree)
            census = HT.validate(DSH_SURFACE, tree, allow_subagents=False)
        self.assertEqual(census["skills"], 1)
        self.assertEqual(census["scripts"], 1)

    def test_validate_rejects_silent_drop(self):
        files = dict(TREE_FILES)
        files["skills/broken/SKILL.md"] = "# broken\nno frontmatter at all"
        with tempfile.TemporaryDirectory() as td:
            tree = HT.read_tree(DSH_SURFACE, _write_tree(Path(td), files))
        with self.assertRaisesRegex(ValueError, "broken"):
            HT.validate(DSH_SURFACE, tree, allow_subagents=False)


class ComposeTests(unittest.TestCase):
    def test_compose_orders_sections_then_indexes(self):
        with tempfile.TemporaryDirectory() as td:
            tree = _write_tree(Path(td), TREE_FILES)
            composed = compose_agents_md(tree)
        self.assertIn("Do the work carefully.", composed)
        self.assertIn("# AVAILABLE SKILLS", composed)
        self.assertIn("`pdf-extract`: extract tables from PDF deliverables", composed)
        self.assertIn("# HELPER SCRIPTS", composed)
        self.assertIn("harness_scripts/", composed)
        self.assertLess(composed.index("Operator"), composed.index("AVAILABLE SKILLS"))
        self.assertLess(composed.index("AVAILABLE SKILLS"), composed.index("HELPER SCRIPTS"))


class MaterialiseTests(unittest.TestCase):
    def test_staging_layout(self):
        sub = DshSubstrate()
        spec = SimpleNamespace(install_mode="dsh_home")
        with tempfile.TemporaryDirectory() as td:
            tree = _write_tree(Path(td) / "tree", TREE_FILES)
            install = sub.materialise(tree, spec, {})
            self.addCleanup(lambda: __import__("shutil").rmtree(install.staging_dir, True))
            agents_md = (install.staging_dir / "home" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("Do the work carefully.", agents_md)
            skill = install.staging_dir / "home" / "skills" / "pdf-extract" / "SKILL.md"
            self.assertTrue(skill.is_file())
            self.assertTrue((install.staging_dir / "scripts" / "tally.py").is_file())
            self.assertTrue(install.fingerprint)

    def test_wrong_install_mode_rejected(self):
        sub = DshSubstrate()
        spec = SimpleNamespace(install_mode="codex_root")
        with tempfile.TemporaryDirectory() as td:
            tree = _write_tree(Path(td) / "tree", TREE_FILES)
            with self.assertRaisesRegex(ValueError, "dsh mode"):
                sub.materialise(tree, spec, {})

    def test_staging_home_dir_jinja(self):
        sub = DshSubstrate()
        spec = SimpleNamespace(install_mode="home_dir_jinja")
        with tempfile.TemporaryDirectory() as td:
            tree = _write_tree(Path(td) / "tree", TREE_FILES)
            install = sub.materialise(tree, spec, {})
            self.addCleanup(lambda: __import__("shutil").rmtree(install.staging_dir, True))
            j2 = install.staging_dir / "_composed" / "prompt.j2"
            self.assertTrue(j2.is_file())
            body = j2.read_text(encoding="utf-8")
            self.assertTrue(body.rstrip().endswith("{{ instruction }}"))
            self.assertIn("`pdf-extract`", body)
            self.assertIn("~/harness_scripts/", body)
            self.assertTrue((install.staging_dir / "skills" / "pdf-extract" / "SKILL.md").is_file())
            self.assertTrue((install.staging_dir / "scripts" / "tally.py").is_file())


EVENTS = [
    {"type": "session", "sessionId": "session-abc", "cwd": "/ws"},
    {"type": "status", "phase": "turn_start", "turn": 1},
    {"type": "status", "phase": "step_start", "turn": 1, "step": 1},
    {"type": "thinking", "text": "plan the extraction"},
    {"type": "text", "text": "Reading the task folder first."},
    {
        "type": "tool_call",
        "callId": "c1",
        "tool": "bash",
        "input": {"command": "ls task_folder"},
    },
    {"type": "tool_result", "callId": "c1", "status": "completed", "result": "a.docx b.pdf"},
    {
        "type": "tool_call",
        "callId": "c2",
        "tool": "read",
        "input": {"file_path": "task_folder/b.pdf"},
    },
    {
        "type": "tool_result",
        "callId": "c2", "status": "error", "result": "",
        "error": {"message": "is a binary file", "code": "EIO"},
    },
    {
        "type": "status", "phase": "step_end", "turn": 1, "step": 1,
        "usage": {"inputTokens": 10, "outputTokens": 5},
    },
    {"type": "final", "sessionId": "session-abc", "reason": None, "text": "done"},
]


class RenderTests(unittest.TestCase):
    def _render(self, events, **kw):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
            path = f.name
        self.addCleanup(Path(path).unlink)
        return render_events(
            Path(path), obs_trunc=kw.get("obs_trunc", 50_000), max_chars=kw.get("max_chars", 10**9)
        )

    def test_shared_grammar(self):
        out = self._render(EVENTS)
        self.assertIn("[HARNESS] session session-abc", out)
        self.assertIn("[THINKING] plan the extraction", out)
        self.assertIn("[ASSISTANT] Reading the task folder first.", out)
        self.assertIn("[ACTION] bash(", out)
        self.assertIn('{"command": "ls task_folder"}', out)
        self.assertIn("[OBSERVATION] a.docx b.pdf", out)
        self.assertIn("[OBSERVATION ERROR]", out)
        self.assertIn("is a binary file", out)
        self.assertIn("[HARNESS] step completed | tokens in=10 out=5", out)
        # The final answer text must NOT be re-emitted after its committed text events.
        self.assertNotIn("[ASSISTANT] done", out)

    def test_turn_error_and_final_error(self):
        events = EVENTS + [
            {
                "type": "status", "phase": "turn_end", "turn": 2,
                "reason": {"kind": "error", "error": {"message": "boom", "code": "X"}},
            },
            {
                "type": "final", "sessionId": "session-abc",
                "reason": {"kind": "error", "error": {"message": "final boom", "code": "X"}},
                "text": "",
            },
        ]
        out = self._render(events)
        self.assertIn("[ERROR] turn ended: boom", out)
        self.assertIn("[ERROR] final: final boom", out)
        self.assertEqual(final_message(Path(self._path_of(events))), "")

    def _path_of(self, events):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_final_message(self):
        self.assertEqual(final_message(Path(self._path_of(EVENTS))), "done")

    def test_truncation_and_empty(self):
        out = self._render([], max_chars=10)
        self.assertEqual(out, "(no parseable events in trajectory)")
        long = [{"type": "text", "text": "x" * 500}]
        out = self._render(long, max_chars=100)
        self.assertIn("render truncation", out)


class RegistryTests(unittest.TestCase):
    def test_tokens_and_types(self):
        self.assertEqual(registry.names(), ("oc", "cx", "dsh"))
        self.assertIsInstance(registry.build("oc"), OpencodeSubstrate)
        self.assertIsInstance(registry.build("cx"), CodexSubstrate)
        self.assertIsInstance(registry.build("dsh"), DshSubstrate)
        with self.assertRaisesRegex(ValueError, "unknown substrate"):
            registry.build("nope")


class JobBenchWiringTests(unittest.TestCase):
    def test_spec_and_adapter(self):
        from harness2.benchmarks.jobbench import DSH_BASE_HARNESS, JobBenchAdapter, make_spec

        spec = make_spec("dsh")
        self.assertEqual(spec.install_mode, "dsh_home")
        self.assertFalse(spec.allow_subagents)
        adapter = JobBenchAdapter(substrate="dsh")
        self.assertEqual(adapter.substrate.name, "dsh")
        self.assertEqual(adapter.substrate.surface().name, "dsh")
        base = adapter.base_harness()
        self.assertEqual(base, DSH_BASE_HARNESS)
        self.assertTrue(HT.is_tree(adapter.substrate.surface(), base))


class LabWiringTests(unittest.TestCase):
    def test_spec_and_adapter(self):
        from harness2.benchmarks.lab import LabAdapter, lab_spec

        spec = lab_spec("dsh")
        self.assertEqual(spec.install_mode, "dsh_home")
        self.assertFalse(spec.allow_subagents)
        self.assertEqual(spec.reserved_scripts, frozenset())
        adapter = LabAdapter(substrate="dsh")
        self.assertEqual(adapter.substrate.name, "dsh")
        self.assertTrue(HT.is_tree(adapter.substrate.surface(), adapter.base_harness()))

    def test_runner_ships_and_compiles(self):
        from harness2 import config

        runner = config.THIRD_PARTY / "lab_runner" / "dsh" / "run_dsh_lab.py"
        self.assertTrue(runner.is_file(), runner)
        compile(runner.read_text(encoding="utf-8"), str(runner), "exec")


class WorkBuddyWiringTests(unittest.TestCase):
    def test_spec_and_adapter(self):
        from harness2.benchmarks.workbuddy import SUBSTRATES, WbAdapter, spec_for

        self.assertIn("dsh", SUBSTRATES)
        spec = spec_for("office", "dsh")
        self.assertEqual(spec.install_mode, "home_dir_jinja")
        self.assertFalse(spec.allow_subagents)
        adapter = WbAdapter("office", substrate="dsh")
        self.assertEqual(adapter.substrate.name, "dsh")
        self.assertTrue(HT.is_tree(adapter.substrate.surface(), adapter.base_harness()))

    def test_harness_configs_ship(self):
        from harness2 import config

        root = config.THIRD_PARTY / "wb_runner"
        for family in ("dsh", "dsh-harness"):
            self.assertTrue((root / "configs" / "harnesses" / family / "_defaults.yaml").is_file())
            entry = root / "configs" / "harnesses" / family / "versions" / "0.2.2.yaml"
            self.assertTrue(entry.is_file())
        self.assertTrue((root / "configs" / "harnesses" / "dsh" / "docker" / "Dockerfile").is_file())
        for agent in ("dsh_agent.py", "dsh_harness_agent.py"):
            p = root / "agents" / agent
            self.assertTrue(p.is_file(), p)
            # harbor is not importable outside the bench repo; a syntax check is the
            # contract this repo can enforce.
            compile(p.read_text(encoding="utf-8"), str(p), "exec")

    def test_job_yaml_dsh(self):
        from harness2.benchmarks.workbuddy import SUBSETS, _job_yaml

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            (ws / "_composed").mkdir(parents=True)
            (ws / "_composed" / "prompt.j2").write_text("{{ instruction }}", encoding="utf-8")
            yaml = _job_yaml(SUBSETS["office"], "dsh", "wb-office-dsh-agg1", ["t1"], ws, 1, Path(td))
        self.assertIn("harness: dsh-harness/0.2.2", yaml)
        self.assertIn("WB_HARNESS_WS", yaml)
        self.assertIn("prompt_template_path", yaml)
        self.assertIn("DSH_PERMISSION_MODE", yaml)
        self.assertIn("host.docker.internal", yaml)

        yaml = _job_yaml(SUBSETS["office"], "dsh", "wb-office-dsh-agg1", ["t1"], None, 1, Path(td))
        self.assertIn("harness: dsh/0.2.2", yaml)
        self.assertNotIn("WB_HARNESS_WS", yaml)
        self.assertIn('DSH_PERMISSION_MODE: "workspace-write"', yaml)


class RunContractTests(unittest.TestCase):
    def test_argv_and_env(self):
        sub = DshSubstrate()
        argv = sub.run_argv()
        self.assertIn("--profile", argv)
        self.assertEqual(argv[argv.index("--profile") + 1], "headless")
        self.assertIn("--json", argv)
        self.assertEqual(argv[argv.index("--approval-policy") + 1], "never")
        self.assertEqual(argv[-1], "-")
        env = sub.run_env({"PATH": "/bin"}, dsh_home=Path("/tmp/h1"))
        self.assertEqual(env["DSH_HOME"], "/tmp/h1")
        self.assertEqual(env["PATH"], "/bin")


if __name__ == "__main__":
    unittest.main()
