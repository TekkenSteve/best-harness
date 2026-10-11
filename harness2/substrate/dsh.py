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

"""DshSubstrate: DeepSeek Harness (`dsh --profile headless`) as a solver substrate.

Carries the c1..c8 surface (DSH_SURFACE) and the one install mode (``dsh_home``):
the always-on text is composed into an isolated ``$DSH_HOME/AGENTS.md``, skills ride
the user-dsh skill root (``$DSH_HOME/skills``), and scripts are staged for delivery
into the rollout workspace. One rollout is one ``dsh`` process whose NDJSON run-event
stream (``--json``) is the trajectory source.

The DSH contracts below were verified against dsh 0.2.2-alpha.2 (source + live probe):

- headless flags: ``--json``, ``--permission-mode <preset>``, ``--approval-policy never``,
  ``--add-dir <path>``, and ``-`` to read the task text from stdin;
- event vocabulary (``dsh-headless/json-stream``): ``session`` | ``status`` | ``text`` |
  ``thinking`` | ``tool_call`` | ``tool_result`` | ``error`` | ``final``, each bounded
  (8 KiB per string, 32 KiB per line) by the writer itself;
- ``AGENTS.md`` is the user-global instruction file read from ``$DSH_HOME``;
- skills load from ``$DSH_HOME/skills`` (the ``user-dsh`` root of dsh-skill-filesystem).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Mapping, Sequence

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest
from .opencode import assert_jinja_safe

# The one placeholder a home_dir_jinja template may contain; the bench renders the
# task instruction into it with StrictUndefined (see assert_jinja_safe).
INSTRUCTION_PLACEHOLDER = "{{ instruction }}"

DSH_SURFACE = SurfaceSpec(
    name="dsh",
    always_on=(
        "systemprompt.md",
        "guardrails.md",
        "LongTermMEMORY.md",
        "tool_descriptions/tool_guidance.md",
    ),
    always_on_max=24_000,
    always_on_cap_reason=(
        "the composed AGENTS.md is injected whole into EVERY turn of the session, so "
        "every token here competes with the agent's own output budget"
    ),
    skill_dir="skills",
    skill_name_rule="must_match",
    trigger_hint=(
        "Fix it by adding a trigger line naming `{name}` to systemprompt.md or "
        'guardrails.md, e.g. "When <the situation this skill handles>, load the '
        '`{name}` skill."'
    ),
    autotrigger_file="guardrails.md",
    trigger_line="- When {desc}, load the `{name}` skill.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=None,
    compile_skill_scripts=True,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 harness_scripts/<name>.py` "
            "in the rollout workspace. Add one by emitting scripts/<name>.py. -->\n",
        ),
        (
            "sub_agents/README.md",
            "<!-- c5: delegated subagents. NOT available on the dsh substrate yet: dsh "
            "teammates are runtime-spawned, not file-configured, so this component is "
            "reserved for a future install mode. -->\n",
        ),
        (
            "skills/README.md",
            "<!-- c6: reusable workflows loaded on demand via the `skill` tool. Add one "
            "as skills/<name>/SKILL.md with frontmatter `name:` matching the directory "
            "and a `description:`; it is staged to $DSH_HOME/skills/<name>/. -->\n",
        ),
    ),
)

HOME_SUBDIR = "home"
SCRIPTS_SUBDIR = "scripts"

_FRONTMATTER_DESC = re.compile(r"^description:\s*(.+)$", re.M)


def _component_index(tree: Path, pattern: str) -> str:
    """One "- name: description" line per component matching `pattern`, or ""."""
    found = sorted(tree.glob(pattern), key=lambda p: p.parent.name)
    out = []
    for p in found:
        m = _FRONTMATTER_DESC.search(p.read_text(errors="replace")[:600])
        desc = m.group(1).strip() if m else "(no description)"
        out.append(f"- `{p.parent.name}`: {desc}")
    return "\n".join(out)


def compose_agents_md(tree: Path) -> str:
    """The always-on text DSH loads as its user-global AGENTS.md.

    Mirrors the opencode ``home_dir_jinja`` composition minus the Jinja placeholder:
    the task arrives through the headless CLI prompt, not this file. Skills are
    advertised by name and description (the load-bearing trigger surface); helper
    scripts are named at the path the launcher puts them in the rollout workspace.
    """
    parts = [
        t.strip()
        for f in DSH_SURFACE.always_on
        if (tree / f).is_file() and (t := (tree / f).read_text(errors="replace").strip())
    ]
    idx = _component_index(tree, "skills/*/SKILL.md")
    if idx:
        parts.append(
            "# AVAILABLE SKILLS\nLoad with the `skill` tool when the situation matches:\n" + idx
        )
    scripts = sorted(p.name for p in tree.glob("scripts/*.py"))
    if scripts:
        parts.append(
            f"# HELPER SCRIPTS\nAvailable at `harness_scripts/` in the working directory: "
            f"{', '.join(scripts)}\n"
            "Run with `python3 harness_scripts/<name>.py`."
        )
    return "\n\n---\n\n".join(parts)


def compose_prompt(tree: Path) -> str:
    """The Jinja template that carries the always-on harness into a WB container run.

    Same contract as the opencode ``home_dir_jinja`` composition: skills and helper
    scripts are advertised by name and description, and the whole template ends with
    the one ``{{ instruction }}`` placeholder the bench renders the task into.
    """
    parts = [
        t.strip()
        for f in DSH_SURFACE.always_on
        if (tree / f).is_file() and (t := (tree / f).read_text(errors="replace").strip())
    ]
    idx = _component_index(tree, "skills/*/SKILL.md")
    if idx:
        parts.append(
            "# AVAILABLE SKILLS\nLoad with the `skill` tool when the situation matches:\n" + idx
        )
    scripts = sorted(p.name for p in tree.glob("scripts/*.py"))
    if scripts:
        parts.append(
            f"# HELPER SCRIPTS\nAvailable at `~/harness_scripts/`: "
            f"{', '.join(scripts)}\n"
            "Run with `python3 ~/harness_scripts/<name>.py`."
        )
    parts.append(INSTRUCTION_PLACEHOLDER)
    return "\n\n---\n\n".join(parts)


class DshSubstrate:
    """dsh headless behind the one Substrate interface; the launcher body is
    benchmark-supplied."""

    name = "dsh"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return DSH_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the harness for one of the two dsh install modes.

        ``dsh_home`` (host rollouts, JobBench/LAB): an isolated DSH_HOME carrying the
        composed AGENTS.md and the skills, plus a scripts dir for workspace delivery:

            <staging>/home/AGENTS.md     composed always-on text (user-global instructions)
            <staging>/home/skills/…      the user-dsh skill root
            <staging>/scripts/…          helper scripts, delivered to <workspace>/harness_scripts

        ``home_dir_jinja`` (WB containers): the composed prompt template plus the
        component dirs the container agent mounts as its harness workspace:

            <staging>/_composed/prompt.j2    always-on text + indexes + {{ instruction }}
            <staging>/skills/…, scripts/…    staged for WB_HARNESS_WS mounting
        """
        tree_dir = Path(tree_dir)
        if not HT.is_tree(DSH_SURFACE, tree_dir):
            raise ValueError(
                f"{tree_dir} is not a dsh harness tree (no always-on file present)"
            )
        staged = Path(tempfile.mkdtemp(prefix=f"adapt-dsh-{spec.install_mode}-"))
        tree = staged / "tree"
        try:
            shutil.copytree(tree_dir, tree)
            for key, body in ctx.items():
                if key.startswith("file:"):
                    rel = HT.normalize_rel(key[len("file:") :])
                    p = tree / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(body, encoding="utf-8")

            if spec.install_mode == "home_dir_jinja":
                composed = compose_prompt(tree)
                assert_jinja_safe(composed)
                out = staged / "_composed" / "prompt.j2"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(composed, encoding="utf-8")
                for d in ("skills", "scripts"):
                    if (tree / d).is_dir():
                        shutil.copytree(tree / d, staged / d)
            elif spec.install_mode == "dsh_home":
                home = staged / HOME_SUBDIR
                home.mkdir()
                (home / "AGENTS.md").write_text(compose_agents_md(tree), encoding="utf-8")
                if (tree / "skills").is_dir():
                    shutil.copytree(tree / "skills", home / "skills")
                if (tree / "scripts").is_dir():
                    shutil.copytree(tree / "scripts", staged / SCRIPTS_SUBDIR)
            else:
                raise ValueError(
                    f"install_mode {spec.install_mode!r} is not a dsh mode; this "
                    f"substrate supports ('dsh_home', 'home_dir_jinja')"
                )
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={"install_mode": spec.install_mode},
        )

    def run_argv(
        self,
        *,
        permission_mode: str | None = None,
        extra_dirs: Sequence[Path] = (),
    ) -> list[str]:
        """The reference headless argv; the task text is piped to stdin via ``-``.

        The permission preset is the measurement fence: ``workspace-write`` keeps the
        agent inside its rollout workspace, and ``--approval-policy never`` is what
        makes an unattended rollout terminate instead of waiting on a prompt.
        """
        argv = [
            str(config.DSH_BIN),
            "--profile",
            "headless",
            "--json",
            "--permission-mode",
            permission_mode or config.DSH_PERMISSION_MODE,
            "--approval-policy",
            "never",
        ]
        for d in extra_dirs:
            argv += ["--add-dir", str(Path(d).resolve())]
        argv.append("-")
        return argv

    def run_env(self, base_env: Mapping[str, str], *, dsh_home: Path) -> dict[str, str]:
        """The reference environment: one isolated DSH_HOME per rollout, so a staged
        harness is the ONLY harness that run sees."""
        env = dict(base_env)
        env["DSH_HOME"] = str(Path(dsh_home).resolve())
        return env

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "DshSubstrate was constructed without a launcher body; the benchmark "
                "adapter must supply one (it owns workspace layout, task prompt and "
                "read-back locations)"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """Check the dsh CLI and its credential; never starts a model session."""
        binary = Path(config.DSH_BIN) if config.DSH_BIN else None
        if binary is None or not binary.is_file():
            raise RuntimeError(
                "dsh not found: install @deepseek-ai/dsh or point HARNESS2_DSH_BIN at "
                "the dsh executable (an isolated DSH_HOME is provisioned per rollout, "
                "so any dsh >= 0.2.2 with the headless profile works)"
            )
        try:
            r = subprocess.run(
                [str(binary), "--version"], capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"dsh --version failed ({type(exc).__name__}); {binary}") from exc
        if r.returncode != 0:
            raise RuntimeError(
                f"dsh --version exited {r.returncode}: {(r.stderr or r.stdout).strip()[-200:]}"
            )
        if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set; an isolated DSH_HOME has no stored "
                "credentials, so the headless rollout authenticates from the launching "
                "environment (export DEEPSEEK_API_KEY)"
            )


def run_rollout(
    argv: list[str],
    *,
    task_text: str,
    cwd: Path,
    env: Mapping[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> int:
    """Run one headless rollout, process-group fenced, NDJSON captured to `stdout_path`.

    Returns the exit code. A timeout kills the whole process group and re-raises, so a
    killed rollout is never mistaken for a completed one by the caller's resume logic.
    """
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        stdout_path.open("w", encoding="utf-8") as out,
        stderr_path.open("w", encoding="utf-8") as err,
    ):
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
        try:
            proc.communicate(task_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            raise
        return proc.returncode


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    finally:
        proc.wait(timeout=30)


_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def _reason_message(reason) -> str:
    if isinstance(reason, dict):
        err = reason.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or err)
        return str(reason.get("message") or reason)
    return str(reason or "")


def iter_events(path: Path) -> list[dict]:
    """The NDJSON run-event stream, junk tolerated, torn last line dropped."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"type": "_read_error", "message": str(e)}]
    events: list[dict] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    """Render a dsh headless --json stream into the shared grammar.

    Mapping (fields per dsh-headless/json-stream): ``text``->ASSISTANT,
    ``thinking``->THINKING, ``tool_call``->ACTION with its parsed ``input``,
    ``tool_result``->OBSERVATION (or OBSERVATION ERROR on ``status: "error"``),
    ``status`` carries turn/step bookkeeping plus per-step usage, ``final`` closes the
    run. The final answer text is NOT re-emitted: committed ``text`` events already
    carry it, and a duplicate would bias downstream contrastive reads.
    """
    lines: list[str] = []
    for ev in iter_events(Path(path)):
        et = ev.get("type")

        if et == "_read_error":
            return f"(failed to read trajectory: {ev.get('message')})"

        if et == "thinking":
            txt = ev.get("text")
            if isinstance(txt, str) and txt.strip():
                lines.append(f"[THINKING] {_clip(txt, obs_trunc)}")
        elif et == "text":
            txt = ev.get("text")
            if isinstance(txt, str) and txt.strip():
                lines.append(f"[ASSISTANT] {_clip(txt, obs_trunc)}")
        elif et == "tool_call":
            name = ev.get("tool") or "tool"
            args = ev.get("input")
            args_s = json.dumps(args, default=str, ensure_ascii=False) if args is not None else ""
            lines.append(f"[ACTION] {name}({_clip(args_s, args_trunc)})")
        elif et == "tool_result":
            body = _clip(ev.get("result"), obs_trunc)
            if ev.get("status") == "error":
                detail = _reason_message(ev.get("error"))
                body = f"{body} | {detail}".strip(" |")
                lines.append(f"[OBSERVATION ERROR] {body}")
            else:
                lines.append(f"[OBSERVATION] {body}")
        elif et == "status":
            phase = ev.get("phase")
            if phase == "step_end" and isinstance(ev.get("usage"), dict):
                u = ev["usage"]
                lines.append(
                    "[HARNESS] step completed | tokens in={i} out={o} cached_read={cr} "
                    "reasoning={r}".format(
                        i=u.get("inputTokens"),
                        o=u.get("outputTokens"),
                        cr=u.get("cacheReadTokens"),
                        r=u.get("reasoningTokens"),
                    )
                )
            elif phase == "turn_end":
                reason = ev.get("reason")
                if isinstance(reason, dict) and reason.get("kind") == "error":
                    lines.append(f"[ERROR] turn ended: {_clip(_reason_message(reason), 1000)}")
        elif et == "session":
            lines.append(f"[HARNESS] session {_clip(ev.get('sessionId'), 100)}")
        elif et == "error":
            lines.append(f"[ERROR] {_clip(_reason_message(ev.get('error')) or ev, 1000)}")
        elif et == "final":
            reason = ev.get("reason")
            if isinstance(reason, dict) and reason.get("kind") == "error":
                lines.append(f"[ERROR] final: {_clip(_reason_message(reason), 1000)}")
        elif et is not None:
            rest = _reason_message(ev.get("message"))
            if rest:
                lines.append(f"[HARNESS] {et}: {_clip(rest, 300)}")

    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


def final_message(path: Path) -> str:
    """The terminal final event's answer text, if the run reached one."""
    last = ""
    for ev in iter_events(Path(path)):
        if ev.get("type") == "final":
            txt = ev.get("text")
            last = txt if isinstance(txt, str) else ""
    return last


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


__all__ = [
    "DSH_SURFACE",
    "DshSubstrate",
    "INSTRUCTION_PLACEHOLDER",
    "compose_agents_md",
    "compose_prompt",
    "final_message",
    "iter_events",
    "render_events",
    "run_rollout",
    "stamp",
]
