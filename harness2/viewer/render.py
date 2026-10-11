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

"""Render a loaded RunTree as one self-contained HTML report.

No scripts, no external assets: collapsibles are native ``<details>`` elements, so
the file works offline from the file system and can be archived next to the runs
directory it describes.
"""

from __future__ import annotations

import html
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .load import Rollout, RunTree, TaskRecord

_KIND_STYLE = {"base": "base", "candidate": "cand", "final": "final"}

_CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0 auto;
       max-width: 1080px; padding: 1rem 1.5rem 4rem; }
h1 { font-size: 1.35rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
h3 { font-size: 1rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
         padding: .25rem .5rem; text-align: left; vertical-align: top; }
th { background: color-mix(in srgb, currentColor 8%, transparent); }
code, pre { font: 12px/1.4 ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
pre { overflow-x: auto; padding: .5rem; border-radius: 4px;
      background: color-mix(in srgb, currentColor 7%, transparent); white-space: pre-wrap; }
.kd-base { opacity: .75; } .kd-cand { } .kd-final { font-weight: 600; }
.delta-up { color: #2e9e5b; } .delta-down { color: #d2492a; }
.muted { opacity: .6; }
details { margin: .35rem 0; }
details > summary { cursor: pointer; padding: .2rem 0; }
.badge { display: inline-block; padding: 0 .4rem; border-radius: 8px; font-size: .8rem;
         border: 1px solid color-mix(in srgb, currentColor 35%, transparent); }
.fb { color: #d2492a; }
header p { color: color-mix(in srgb, currentColor 70%, transparent); }
"""


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _pct(frac: object) -> str:
    if isinstance(frac, (int, float)):
        return f"{float(frac) * 100:.1f}%"
    return "—"


def _delta(refined: object, base: object) -> str:
    if isinstance(refined, (int, float)) and isinstance(base, (int, float)):
        d = (float(refined) - float(base)) * 100
        cls = "delta-up" if d >= 0 else "delta-down"
        return f'<span class="{cls}">{d:+.1f}</span>'
    return "—"


def _mode_cell(task: TaskRecord, mode: str) -> str:
    sel = task.selections.get(mode)
    if sel is None:
        return '<td class="muted">—</td>'
    chosen = sel["chosen_label"] or "?"
    kind = "base" if sel["chosen_is_base"] else "final"
    flag = ' <span class="fb">fb</span>' if sel["fallback"] else ""
    j, jb = task.judged.get(mode), task.judged_base.get(mode)
    return (
        f"<td>{_kind_span(chosen, kind)}{flag}"
        f"<br><span class='muted'>{_pct(j and j['frac'])} vs base {_pct(jb and jb['frac'])}"
        f" {_delta(j and j['frac'], jb and jb['frac'])}</span></td>"
    )


def _kind_span(label: str, kind: str) -> str:
    return f'<span class="kd-{_KIND_STYLE.get(kind, "cand")}">{_e(label)}</span>'


_BAR_W = 26
_GAP = 18
_AXIS_H = 96
_LABEL_H = 34
_CHART_H = _AXIS_H + _LABEL_H + 26


def _bar(x: float, frac: float, color: str) -> str:
    h = max(1.5, round(frac * _AXIS_H))
    y = _AXIS_H + 10 - h
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{_BAR_W}" height="{h:.1f}" rx="1.5" fill="{color}"/>'


def _score_chart(pairs: list[tuple[str, object, object]]) -> str:
    """Paired base-vs-refined bars for one domain; only tasks judged on both sides."""
    pairs = [(t, a, b) for t, a, b in pairs if isinstance(a, (int, float)) and isinstance(b, (int, float))]
    if len(pairs) < 1:
        return ""
    step = _BAR_W * 2 + _GAP
    w = step * len(pairs) + _GAP
    parts = [f'<svg viewBox="0 0 {w} {_CHART_H}" width="100%" preserveAspectRatio="xMinYMax meet" role="img" aria-label="base vs refined scores">']
    for i, (task, base, refined) in enumerate(pairs):
        x = _GAP + i * step
        parts.append(_bar(x, float(base), "color-mix(in srgb, currentColor 35%, transparent)"))
        d = float(refined) - float(base)
        parts.append(_bar(x + _BAR_W, float(refined), "#2e9e5b" if d >= 0 else "#d2492a"))
        parts.append(
            f'<text x="{x + _BAR_W:.1f}" y="{_AXIS_H + 22}" font-size="10" text-anchor="middle" '
            f'fill="{"#2e9e5b" if d >= 0 else "#d2492a"}">{d * 100:+.0f}</text>'
        )
        name = task if len(task) <= 16 else task[:15] + "…"
        parts.append(
            f'<text x="{x + _BAR_W:.1f}" y="{_AXIS_H + 34}" font-size="9" text-anchor="middle" '
            f'fill="color-mix(in srgb, currentColor 70%, transparent)">{_e(name)}</text>'
        )
    parts.append(
        f'<line x1="{_GAP / 2}" y1="{_AXIS_H + 10}" x2="{w - _GAP / 2}" y2="{_AXIS_H + 10}" '
        'stroke="color-mix(in srgb, currentColor 35%, transparent)"/>'
    )
    parts.append(
        f'<text x="{_GAP / 2}" y="8" font-size="9" fill="color-mix(in srgb, currentColor 60%, transparent)">'
        "□ base　■ refined (green = gain)</text>"
    )
    parts.append("</svg>")
    return "".join(parts)


def _gain_chart(domains: dict[str, float]) -> str:
    """Namespace header chart: mean refined−base gain per domain, horizontal bars."""
    if not domains:
        return ""
    w, row_h = 360, 22
    h = row_h * len(domains) + 8
    lo, hi = min(domains.values(), default=0), max(domains.values(), default=0)
    span = max(hi - lo, 0.05)
    zero = 40 + (0 - lo) / span * (w - 80)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="mean gain per domain">']
    for i, (domain, gain) in enumerate(sorted(domains.items(), key=lambda kv: -kv[1])):
        y = 6 + i * row_h
        length = abs(gain - 0) / span * (w - 80) * 0.9
        x = zero if gain >= 0 else zero - length
        color = "#2e9e5b" if gain >= 0 else "#d2492a"
        parts.append(
            f'<rect x="{x:.1f}" y="{y + 3}" width="{max(length, 1.5):.1f}" height="12" rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{x - 4 if gain < 0 else x + length + 4:.1f}" y="{y + 13}" font-size="10" '
            f'text-anchor="{"end" if gain < 0 else "start"}" fill="{color}">{gain * 100:+.1f}</text>'
        )
        parts.append(
            f'<text x="4" y="{y + 13}" font-size="10" fill="color-mix(in srgb, currentColor 70%, transparent)">'
            f"{_e(domain if len(domain) <= 22 else domain[:21] + '…')}</text>"
        )
    parts.append(f'<line x1="{zero:.1f}" y1="2" x2="{zero:.1f}" y2="{h - 2}" stroke="color-mix(in srgb, currentColor 35%, transparent)"/>')
    parts.append("</svg>")
    return "".join(parts)


def _rollout_details(roll: Rollout) -> str:
    head = (
        f"{_kind_span(roll.label, roll.kind)}"
        + (f' <span class="badge">{_e(roll.dim)}</span>' if roll.dim else "")
        + (' <span class="fb">fell back</span>' if roll.fell_back else "")
        + (' <span class="fb">kept base</span>' if roll.kept_base else "")
    )
    body = ""
    if roll.notes:
        body += f"<h4>Proposer notes</h4><pre>{_e(roll.notes)}</pre>"
    if roll.harness_diff:
        body += f"<h4>Harness diff</h4><pre>{_e(roll.harness_diff)}</pre>"
    if roll.deliverable:
        body += f"<h4>Deliverable</h4><pre>{_e(roll.deliverable)}</pre>"
    if roll.trajectory:
        body += f"<h4>Trajectory ({_e(roll.trajectory_name)})</h4><pre>{_e(roll.trajectory)}</pre>"
    if not body:
        body = '<p class="muted">No evidence files found for this rollout.</p>'
    return f"<details><summary>{head}</summary>{body}</details>"


def _task_section(task: TaskRecord) -> str:
    title = f"[{task.status or '?'}] {_e(task.task_id)}"
    sel_blocks = ""
    for mode, sel in sorted(task.selections.items()):
        if sel is None:
            continue
        sel_blocks += (
            f"<h4>Blind selection ({_e(mode)})</h4>"
            f"<p>Chose {_kind_span(sel['chosen_label'], 'base' if sel['chosen_is_base'] else 'final')}"
            f" — <i>{_e(sel['why'] or sel['error'] or 'no reason recorded')}</i></p>"
        )
    experience = ""
    if task.experience:
        blocks = "".join(f"<pre>{_e(e)}</pre>" for e in task.experience)
        experience = f"<h4>Experience</h4>{blocks}"
    rollouts = "".join(_rollout_details(r) for r in task.rollouts)
    return (
        f'<details><summary>{title}</summary>'
        f"<p class='muted'>namespace {_e(task.namespace)} · domain {_e(task.domain)} · k={task.k}</p>"
        f"{sel_blocks}{experience}<h4>Rollouts ({len(task.rollouts)})</h4>{rollouts}"
        "</details>"
    )


def _domain_table(tasks: Iterable[TaskRecord], mode: str) -> str:
    """One domain's table for one mode, with its score chart when judged."""
    tasks = sorted(tasks, key=lambda t: t.task_id)
    head = "<tr><th>task</th><th>status</th><th>pick · score vs base</th></tr>"
    rows = ""
    for t in tasks:
        cells = _mode_cell(t, mode)
        rows += f"<tr><td>{_e(t.task_id)}</td><td>{_e(t.status)}</td>{cells}</tr>"
    chart = _score_chart(
        [
            (
                t.task_id,
                (t.judged_base.get(mode) or {}).get("frac"),
                (t.judged.get(mode) or {}).get("frac"),
            )
            for t in tasks
        ]
    )
    judged_pairs = [
        (t.judged[mode]["frac"], t.judged_base[mode]["frac"])
        for t in tasks
        if t.judged.get(mode) and t.judged_base.get(mode)
        and isinstance(t.judged[mode].get("frac"), (int, float))
        and isinstance(t.judged_base[mode].get("frac"), (int, float))
    ]
    gains = ""
    if judged_pairs:
        mean = sum(a for a, _ in judged_pairs) / len(judged_pairs)
        mean_b = sum(b for _, b in judged_pairs) / len(judged_pairs)
        gains = (
            f"<p>{_e(mode)}: refined {_pct(mean)} vs base {_pct(mean_b)} mean over "
            f"{len(judged_pairs)} judged task(s) — {_delta(mean, mean_b)} pts</p>"
        )
    return f"{chart}<table>{head}{rows}</table>{gains}"


def _ns_gain_domains(tasks: list[TaskRecord]) -> dict[str, float]:
    """Mean refined−base gain per domain over tasks judged on both sides (agg mode)."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for t in tasks:
        j, jb = t.judged.get("agg"), t.judged_base.get("agg")
        if (
            isinstance(j, dict)
            and isinstance(jb, dict)
            and isinstance(j.get("frac"), (int, float))
            and isinstance(jb.get("frac"), (int, float))
        ):
            domain = t.domain or "(none)"
            sums[domain] = sums.get(domain, 0.0) + float(j["frac"]) - float(jb["frac"])
            counts[domain] = counts.get(domain, 0) + 1
    return {domain: total / counts[domain] for domain, total in sums.items()}


def render(tree: RunTree) -> str:
    """The whole report as one HTML document string."""
    by_ns: dict[str, list[TaskRecord]] = defaultdict(list)
    for t in tree.tasks:
        by_ns[t.namespace].append(t)
    sections = ""
    for ns in sorted(by_ns):
        tasks = by_ns[ns]
        parts = [f"<h2>{_e(ns)} · {len(tasks)} task(s)</h2>"]
        gain_chart = _gain_chart(_ns_gain_domains(tasks))
        if gain_chart:
            parts.append(f"<h3>mean gain per domain (parallel mode)</h3>{gain_chart}")
        by_domain: dict[str, list[TaskRecord]] = defaultdict(list)
        for t in tasks:
            by_domain[t.domain or "(none)"].append(t)
        modes = sorted({m for t in tasks for m in t.selections}, key=lambda m: ("agg" != m, m))
        for domain in sorted(by_domain):
            domain_tasks = sorted(by_domain[domain], key=lambda x: x.task_id)
            for mode in modes:
                parts.append(f"<h3>domain: {_e(domain)} · {_e(mode)}</h3>")
                parts.append(_domain_table(domain_tasks, mode))
            parts.extend(_task_section(t) for t in domain_tasks)
        sections += "".join(parts)
    if not tree.tasks:
        sections = "<p>No summary.json found under this runs root.</p>"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Harness² runs report</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<header><h1>Harness² runs report</h1>"
        f"<p>{_e(tree.root)} · {len(tree.tasks)} task record(s) · generated {stamp}</p></header>"
        f"{sections}</body></html>"
    )


def write(tree: RunTree, out) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(tree), encoding="utf-8")
    return out


__all__ = ["render", "write"]
