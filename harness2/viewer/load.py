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

"""Load a runs/ tree into the records the viewer renders.

The on-disk contracts are the ones the method itself writes: per-task
``summary.json`` (rollouts, stages, finals), per-mode ``selection.json`` (the blind
pick and its why), ``judged.json`` / ``base_judged.json`` (post-selection scores),
and the per-label evidence files (deliverables, trajectories, ``harness.diff``).
Everything is best-effort: a run tree missing any piece still renders, with the
missing piece shown as absent rather than fabricated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_SNIPPET = 8_000
MAX_LISTING = 20


def _read_json(p: Path) -> dict | None:
    try:
        got = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return got if isinstance(got, dict) else None


def _read_json_rows(p: Path) -> list:
    """judged.json / base_judged.json are ARRAYS of row objects."""
    try:
        got = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    return got if isinstance(got, list) else []


def _snippet(p: Path, limit: int = MAX_SNIPPET) -> str:
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > limit:
        return text[:limit] + f"\n…(+{len(text) - limit} chars — truncated)"
    return text


@dataclass
class Rollout:
    """One rollout of one task: a label plus whatever evidence was found for it."""

    label: str
    kind: str  # base | candidate | final
    dim: str = ""
    notes: str = ""
    fell_back: bool = False
    kept_base: bool = False
    run_dir: Path | None = None
    deliverable: str = ""
    trajectory: str = ""
    trajectory_name: str = ""
    harness_diff: str = ""


@dataclass
class TaskRecord:
    namespace: str
    task_id: str
    domain: str = ""
    status: str = ""
    k: int = 0
    rollouts: list[Rollout] = field(default_factory=list)
    # mode -> {"chosen_label", "chosen_is_base", "why", "ok", "fallback", "error"}
    selections: dict[str, dict] = field(default_factory=dict)
    # mode -> {"frac", "all_pass", "n_passed", "n_criteria"} or None; same for base
    judged: dict[str, dict | None] = field(default_factory=dict)
    judged_base: dict[str, dict | None] = field(default_factory=dict)
    experience: list[str] = field(default_factory=list)


@dataclass
class RunTree:
    root: Path
    tasks: list[TaskRecord] = field(default_factory=list)

    def namespaces(self) -> list[str]:
        return sorted({t.namespace for t in self.tasks})


def _ns_of(path: Path, root: Path) -> str:
    """The namespace token governing a file: the first path segment below the runs
    root's summary area that matches the <bench>-<substrate>-<mode><k> shape."""
    rel = path.relative_to(root)
    for part in rel.parts:
        if "-agg" in part or "-seq" in part:
            return part
    return rel.parts[0] if rel.parts else ""


def _label_evidence(work: Path) -> dict[str, Path]:
    """label -> harness.diff, from any pool/<label>/harness.diff under the work dir."""
    out: dict[str, Path] = {}
    for p in work.rglob("harness.diff"):
        parts = p.parts
        if len(parts) >= 2 and parts[-3] == "pool":
            out.setdefault(parts[-2], p)
    return out


def _trajectory_for(run_dir: Path) -> tuple[str, str]:
    """(text, name) of the run's trajectory: in the run dir, or the JobBench sibling
    store (model_output/<label> keeps deliverables, model_traj/<label> the stream)."""
    for name in ("trajectory.md", "trajectory.jsonl"):
        p = run_dir / name
        if p.is_file():
            return _snippet(p), name
    streams = sorted(run_dir.glob("*.jsonl"))
    if streams:
        return _snippet(streams[-1]), streams[-1].name
    if "model_output" in run_dir.parts:  # the JobBench store layout
        parts = list(run_dir.parts)
        parts[parts.index("model_output")] = "model_traj"
        sibling = Path(*parts)
        if sibling.is_dir():
            streams = sorted(sibling.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            if streams:
                return _snippet(streams[-1]), streams[-1].name
    return "", ""


def _deliverable_for(run_dir: Path) -> str:
    for name in ("answer.txt", "INDEX.md"):
        p = run_dir / name
        if p.is_file():
            return _snippet(p)
    texts = [p for p in sorted(run_dir.rglob("*")) if p.is_file() and p.suffix in (".md", ".txt")]
    return "\n\n".join(f"--- {p.name} ---\n{_snippet(p, 2_000)}" for p in texts[:MAX_LISTING])


def _evidence(run_dir: Path | None) -> tuple[str, str, str]:
    """(deliverable, trajectory, trajectory_name) for a run dir, all best-effort."""
    if run_dir is None:
        return "", "", ""
    trajectory, name = _trajectory_for(run_dir)
    return _deliverable_for(run_dir), trajectory, name


def _rollout_from_stage(stage: dict, run_dirs: dict[str, Path], diffs: dict[str, Path]) -> Rollout:
    label = str(stage.get("label") or "")
    run_dir = run_dirs.get(label)
    deliverable, trajectory, trajectory_name = _evidence(run_dir)
    roll = Rollout(
        label=label,
        kind="candidate",
        dim=str(stage.get("dim") or ""),
        notes=str(stage.get("notes") or ""),
        fell_back=bool(stage.get("fell_back")),
        kept_base=bool(stage.get("kept_base")),
        run_dir=run_dir,
        deliverable=deliverable,
        trajectory=trajectory,
        trajectory_name=trajectory_name,
    )
    if label in diffs:
        roll.harness_diff = _snippet(diffs[label])
    return roll


def _load_task(summary_path: Path, root: Path) -> TaskRecord:
    data = _read_json(summary_path) or {}
    work = summary_path.parent
    rec = TaskRecord(
        namespace=_ns_of(summary_path, root),
        task_id=str(data.get("task_id") or work.name),
        domain=str(data.get("domain") or ""),
        status=str(data.get("status") or ""),
        k=int(data.get("k") or 0),
    )
    run_dirs: dict[str, Path] = {}
    for b in data.get("base") or []:
        if isinstance(b, dict) and b.get("label"):
            try:
                run_dirs[str(b["label"])] = Path(str(b["run_dir"]))
            except (KeyError, TypeError):
                pass
    # Candidates and finals carry no run_dir in the summary; every benchmark keeps
    # one label per directory beside the base labels (executions/<ns>/<task>/<label>,
    # model_output/<label>, results/<substrate>/<run_id>), so derive siblings from a
    # base run_dir when one exists.
    labels: set[str] = set(run_dirs)
    for mode_rec in (data.get("modes") or {}).values():
        if isinstance(mode_rec, dict):
            labels.update(str(s["label"]) for s in mode_rec.get("stages") or [] if isinstance(s, dict) and s.get("label"))
            labels.update(str(x) for x in mode_rec.get("finals") or [])
    if run_dirs:
        parent = next(iter(run_dirs.values())).parent
        first_base = next(iter(run_dirs.values()))
        # A summary keeps BOTH modes' records but lives in the parallel namespace's
        # dir, so the other mode's rollouts sit under the sibling namespace
        # (<ns>-agg<k> <-> <ns>-seq<k>): swap that path component when present.
        ns = rec.namespace
        alt_ns = ns.replace("-agg", "-seq") if "-agg" in ns else ns.replace("-seq", "-agg")
        parts = first_base.parts
        ns_positions = [i for i, part in enumerate(parts) if part == ns]
        for label in labels:
            path = parent / label
            if not path.is_dir() and alt_ns != ns and ns_positions:
                i = ns_positions[0]
                alt = Path(*parts[:i], alt_ns, *parts[i + 1 : -1], label)
                if alt.is_dir():
                    path = alt
            run_dirs.setdefault(label, path)
    diffs = _label_evidence(work)
    merged: dict[str, Rollout] = {}

    def _add(roll: Rollout) -> None:
        prior = merged.get(roll.label)
        if prior is None:
            merged[roll.label] = roll
            return
        if roll.kind == "final":
            prior.kind = "final"
        prior.dim = prior.dim or roll.dim
        prior.notes = prior.notes or roll.notes
        prior.harness_diff = prior.harness_diff or roll.harness_diff
        prior.deliverable = prior.deliverable or roll.deliverable
        prior.trajectory = prior.trajectory or roll.trajectory
        prior.trajectory_name = prior.trajectory_name or roll.trajectory_name

    for b in data.get("base") or []:
        if not isinstance(b, dict):
            continue
        label = str(b.get("label") or "")
        run_dir = run_dirs.get(label)
        deliverable, trajectory, trajectory_name = _evidence(run_dir)
        _add(
            Rollout(
                label=label,
                kind="base",
                run_dir=run_dir,
                deliverable=deliverable,
                trajectory=trajectory,
                trajectory_name=trajectory_name,
            )
        )
    for mode_rec in (data.get("modes") or {}).values():
        if not isinstance(mode_rec, dict):
            continue
        for stage in mode_rec.get("stages") or []:
            if isinstance(stage, dict) and stage.get("label"):
                _add(_rollout_from_stage(stage, run_dirs, diffs))
        for label in mode_rec.get("finals") or []:
            run_dir = run_dirs.get(str(label))
            deliverable, trajectory, trajectory_name = _evidence(run_dir)
            _add(
                Rollout(
                    label=str(label),
                    kind="final",
                    dim="",
                    run_dir=run_dir,
                    deliverable=deliverable,
                    trajectory=trajectory,
                    trajectory_name=trajectory_name,
                    harness_diff=_snippet(diffs[str(label)]) if str(label) in diffs else "",
                )
            )
    rec.rollouts = list(merged.values())
    for sel_path in sorted(work.glob("selection/*/selection.json")):
        sel = _read_json(sel_path) or {}
        mode = str(sel.get("mode") or sel_path.parent.name)
        rec.selections[mode] = {
            "chosen_label": str(sel.get("chosen_label") or ""),
            "chosen_is_base": bool(sel.get("chosen_is_base")),
            "why": str(sel.get("why") or ""),
            "ok": bool(sel.get("ok")),
            "fallback": bool(sel.get("fallback")),
            "error": str(sel.get("error") or ""),
        }
        exp = sel.get("experience") or {}
        if isinstance(exp, dict) and exp.get("lesson"):
            rec.experience.append(
                f"cue: {exp.get('cue', '')}\nlesson: {exp.get('lesson', '')}\n"
                f"evidence: {exp.get('evidence', '')}\nconfidence: {exp.get('confidence', '')}"
            )
    for p in sorted(work.rglob("EXPERIENCE.md"))[:5]:
        body = _snippet(p, 2_000)
        if body:
            rec.experience.append(f"--- {p.relative_to(work)} ---\n{body}")
    return rec


def _attach_judged(tree: RunTree) -> None:
    """Join judged.json / base_judged.json rows onto tasks by (namespace, task, mode)."""
    index: dict[tuple[str, str, str], dict] = {}
    base_index: dict[tuple[str, str, str], dict] = {}
    for name, sink in (("judged.json", index), ("base_judged.json", base_index)):
        for p in tree.root.rglob(name):
            ns = _ns_of(p, tree.root)
            for row in _read_json_rows(p):
                if isinstance(row, dict) and row.get("graded"):
                    sink[(ns, str(row.get("task_id")), str(row.get("mode")))] = {
                        "frac": row.get("frac"),
                        "all_pass": bool(row.get("all_pass")),
                        "n_passed": row.get("n_passed"),
                        "n_criteria": row.get("n_criteria"),
                    }
    for task in tree.tasks:
        for mode in task.selections:
            task.judged[mode] = index.get((task.namespace, task.task_id, mode))
            task.judged_base[mode] = base_index.get((task.namespace, task.task_id, mode))


def load(root: Path | str) -> RunTree:
    """Scan a runs root (any benchmark's, or the demo's) into renderable records."""
    root = Path(root).resolve()
    tree = RunTree(root=root)
    for summary in sorted(root.rglob("summary.json")):
        tree.tasks.append(_load_task(summary, root))
    _attach_judged(tree)
    return tree


__all__ = ["Rollout", "TaskRecord", "RunTree", "load"]
