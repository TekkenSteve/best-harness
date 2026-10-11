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

No scripts, no external assets: collapsibles are native ``<details>`` elements and
charts are hand-drawn SVG with explicit width/height attributes (a bare
``width="100%"`` SVG collapses to zero height in browsers), so the file works
offline from the file system and can be archived next to the runs directory it
describes.
"""

from __future__ import annotations

import html
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .load import Rollout, RunTree, TaskRecord

_CSS = """
:root {
  --bg: #f5f6f8; --card: #ffffff; --border: #e4e7ec; --text: #1c1e21;
  --muted: #6b7280; --accent: #4f7cff; --up: #1d9d55; --down: #d64541;
  --chip-base: #8a8f98; --chip-cand: #4f7cff; --chip-final: #1d9d55;
  --pre-bg: #f1f3f6;
  --tg-action: #4f7cff; --tg-obs: #6b7280; --tg-err: #d64541;
  --tg-assist: #1d9d55; --tg-think: #8b5cf6; --tg-user: #d97706;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141517; --card: #1e2024; --border: #30333a; --text: #e8eaed;
    --muted: #9aa0a8; --accent: #7aa2ff; --up: #3ecf72; --down: #ef6b66;
    --chip-base: #8a8f98; --chip-cand: #7aa2ff; --chip-final: #3ecf72;
    --pre-bg: #17181c;
  }
}
* { box-sizing: border-box; }
body { font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif;
       background: var(--bg); color: var(--text); margin: 0;
       padding: 2rem 1.25rem 5rem; }
.page { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .15rem; letter-spacing: -0.01em; }
h2 { font-size: 1.15rem; margin: 2.2rem 0 .8rem; padding-bottom: .4rem;
     border-bottom: 2px solid var(--accent); display: inline-block; }
h3 { font-size: .98rem; margin: 1.6rem 0 .6rem; }
h4 { font-size: .82rem; margin: .9rem 0 .35rem; text-transform: uppercase;
     letter-spacing: .04em; color: var(--muted); }
.muted { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 10px; padding: 1rem 1.25rem; margin: .8rem 0;
        box-shadow: 0 1px 2px rgba(16, 24, 40, .05); }
.chips { display: flex; flex-wrap: wrap; gap: .6rem; margin: 1rem 0 .5rem; }
.chip { background: var(--card); border: 1px solid var(--border);
        border-radius: 10px; padding: .5rem .9rem; min-width: 8.5rem; }
.chip .v { font-size: 1.25rem; font-weight: 650; letter-spacing: -0.01em; }
.chip .l { font-size: .72rem; color: var(--muted); text-transform: uppercase;
           letter-spacing: .05em; margin-top: .1rem; }
table { border-collapse: separate; border-spacing: 0; width: 100%;
        margin: .6rem 0 1rem; font-size: .92rem; }
th, td { padding: .5rem .7rem; text-align: left; vertical-align: top;
         border-bottom: 1px solid var(--border); }
th { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
     color: var(--muted); border-bottom: 1.5px solid var(--border); }
tr:last-child td { border-bottom: none; }
tbody tr:hover { background: color-mix(in srgb, var(--accent) 6%, transparent); }
code, pre { font: 12.5px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
pre { overflow: auto; max-height: 460px; padding: .7rem .85rem; border-radius: 8px;
      background: var(--pre-bg); border: 1px solid var(--border);
      white-space: pre-wrap; word-break: break-word; margin: .25rem 0 .6rem; }
.badge { display: inline-block; padding: .05rem .5rem; border-radius: 99px;
         font-size: .72rem; font-weight: 600; letter-spacing: .02em; }
.b-base { color: var(--chip-base); border: 1px solid var(--chip-base); }
.b-cand { color: var(--chip-cand); border: 1px solid var(--chip-cand); }
.b-final { color: var(--chip-final); border: 1px solid var(--chip-final); }
.b-warn { color: var(--down); border: 1px solid var(--down); }
.b-dim { color: var(--muted); border: 1px solid var(--border); }
.delta-up { color: var(--up); font-weight: 600; }
.delta-down { color: var(--down); font-weight: 600; }
details { margin: .5rem 0; border: 1px solid var(--border); border-radius: 10px;
          background: var(--card); }
details > summary { cursor: pointer; padding: .65rem 1rem; list-style: none;
                    display: flex; align-items: center; gap: .5rem; }
details > summary::before { content: "▸"; color: var(--muted); font-size: .8rem;
                            transition: transform .12s; }
details[open] > summary::before { transform: rotate(90deg); }
details > summary::-webkit-details-marker { display: none; }
details > .body { padding: 0 1rem 1rem; border-top: 1px solid var(--border); }
details > .body > :first-child { margin-top: .8rem; }
details.roll { margin-left: .8rem; border-radius: 8px; }
details.roll > summary { padding: .45rem .8rem; font-size: .9rem; }
.tg { font-weight: 700; }
.tg-action { color: var(--tg-action); } .tg-obs { color: var(--tg-obs); }
.tg-err { color: var(--tg-err); } .tg-assist { color: var(--tg-assist); }
.tg-think { color: var(--tg-think); } .tg-user { color: var(--tg-user); }
.d-add { color: var(--up); } .d-del { color: var(--down); }
.d-hunk { color: var(--accent); }
svg.chart { display: block; max-width: 100%; height: auto; margin: .4rem 0 .6rem; }
figcaption, .chart-note { font-size: .78rem; color: var(--muted); margin: .1rem 0 .8rem; }
footer { margin-top: 3rem; font-size: .78rem; color: var(--muted);
         border-top: 1px solid var(--border); padding-top: .8rem; }
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


# ----------------------------------------------------------------------------- charts

_AXIS_H = 190
_PAD_L, _PAD_B, _PAD_T = 46, 34, 30
_PAIR_W, _BAR_W = 46, 17

_C_GRID = "var(--border)"
_C_AXIS_TXT = "var(--muted)"
_C_BASE = "#9aa0a8"
_C_UP = "var(--up)"
_C_DOWN = "var(--down)"


def _y(frac: float) -> float:
    return _PAD_T + (1 - frac) * _AXIS_H


def _score_chart(pairs: list[tuple[str, object, object]]) -> str:
    """Paired base-vs-refined bars for one domain; only tasks judged on both sides."""
    pairs = [
        (t, float(a), float(b))
        for t, a, b in pairs
        if isinstance(a, (int, float)) and isinstance(b, (int, float))
    ]
    if not pairs:
        return ""
    n = len(pairs)
    plot_w = max(n * _PAIR_W + 30, 120)
    w, h = _PAD_L + plot_w + 12, _PAD_T + _AXIS_H + _PAD_B
    parts = [
        f'<svg class="chart" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        'role="img" aria-label="base vs refined scores">'
    ]
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        y = _y(v)
        parts.append(
            f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_PAD_L + plot_w}" y2="{y:.1f}" '
            f'stroke="{_C_GRID}" stroke-width="{1.2 if v == 0 else 0.6}"/>'
        )
        parts.append(
            f'<text x="{_PAD_L - 6}" y="{y + 3:.1f}" font-size="9.5" text-anchor="end" '
            f'fill="{_C_AXIS_TXT}">{v:.0%}</text>'
        )
    for i, (task, base, refined) in enumerate(pairs):
        cx = _PAD_L + 16 + i * _PAIR_W + _PAIR_W / 2
        d = refined - base
        for frac, color, off in (
            (base, _C_BASE, -_BAR_W / 2 - 1),
            (refined, _C_UP if d >= 0 else _C_DOWN, _BAR_W / 2 + 1),
        ):
            bh = max(1.5, frac * _AXIS_H)
            parts.append(
                f'<rect x="{cx + off - _BAR_W / 2:.1f}" y="{_y(frac):.1f}" '
                f'width="{_BAR_W}" height="{bh:.1f}" rx="2.5" fill="{color}"/>'
            )
        parts.append(
            f'<text x="{cx:.1f}" y="{_PAD_T + _AXIS_H + 13}" font-size="10" '
            f'font-weight="600" text-anchor="middle" '
            f'fill="{_C_UP if d >= 0 else _C_DOWN}">{d * 100:+.0f}</text>'
        )
        name = task.rsplit("/", 1)[-1]
        name = name if len(name) <= 10 else name[:9] + "…"
        parts.append(
            f'<text x="{cx:.1f}" y="{_PAD_T + _AXIS_H + 26}" font-size="9" '
            f'text-anchor="middle" fill="{_C_AXIS_TXT}">{_e(name)}</text>'
        )
    lx = _PAD_L + plot_w - 130
    parts.append(
        f'<rect x="{lx}" y="8" width="9" height="9" rx="2" fill="{_C_BASE}"/>'
        f'<text x="{lx + 13}" y="16" font-size="10" fill="{_C_AXIS_TXT}">base</text>'
        f'<rect x="{lx + 60}" y="8" width="9" height="9" rx="2" fill="{_C_UP}"/>'
        f'<text x="{lx + 73}" y="16" font-size="10" fill="{_C_AXIS_TXT}">refined</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _gain_chart(domains: dict[str, float]) -> str:
    """Namespace header chart: mean refined−base gain per domain, horizontal bars."""
    if not domains:
        return ""
    label_w, row_h = 190, 30
    plot_w = 420
    w = label_w + plot_w + 66
    h = row_h * len(domains) + 16
    lo = min(0.0, *domains.values())
    hi = max(0.0, *domains.values())
    span = max(hi - lo, 0.05)
    zero = label_w + (0 - lo) / span * plot_w
    parts = [
        f'<svg class="chart" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        'role="img" aria-label="mean gain per domain">'
    ]
    parts.append(
        f'<line x1="{zero:.1f}" y1="4" x2="{zero:.1f}" y2="{h - 4}" '
        f'stroke="{_C_GRID}" stroke-width="1.2"/>'
    )
    for i, (domain, gain) in enumerate(sorted(domains.items(), key=lambda kv: -kv[1])):
        y = 8 + i * row_h
        length = abs(gain) / span * plot_w
        x = zero if gain >= 0 else zero - length
        color = _C_UP if gain >= 0 else _C_DOWN
        parts.append(
            f'<rect x="{x:.1f}" y="{y + 3}" width="{max(length, 2):.1f}" height="15" '
            f'rx="3" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{label_w - 8}" y="{y + 15}" font-size="10.5" text-anchor="end" '
            f'fill="var(--text)">'
            f'{_e(domain if len(domain) <= 26 else domain[:25] + "…")}</text>'
        )
        parts.append(
            f'<text x="{x + length + 7 if gain >= 0 else x - 7:.1f}" y="{y + 15}" '
            f'font-size="10.5" font-weight="600" '
            f'text-anchor="{"start" if gain >= 0 else "end"}" fill="{color}">'
            f"{gain * 100:+.1f}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------------------- evidence

_TAG_RE = re.compile(
    r"\[(ACTION|OBSERVATION ERROR|OBSERVATION|ERROR|ASSISTANT|THINKING|REASONING"
    r"|HARNESS(?: [A-Za-z0-9.]*)?|USER)\]"
)
_TAG_CLASS = {
    "ACTION": "tg-action",
    "OBSERVATION": "tg-obs",
    "OBSERVATION ERROR": "tg-err",
    "ERROR": "tg-err",
    "ASSISTANT": "tg-assist",
    "THINKING": "tg-think",
    "REASONING": "tg-think",
    "USER": "tg-user",
}


def _traj_html(text: str) -> str:
    def repl(m: re.Match) -> str:
        tag = m.group(1)
        cls = _TAG_CLASS.get(tag, _TAG_CLASS.get(tag.split()[0], "tg-obs"))
        return f'<span class="tg {cls}">[{_e(tag)}]</span>'

    return _TAG_RE.sub(repl, _e(text))


def _diff_html(text: str) -> str:
    out = []
    for ln in text.splitlines():
        esc = _e(ln)
        if ln.startswith(("+++", "---", "@@")):
            out.append(f'<span class="d-hunk">{esc}</span>')
        elif ln.startswith("+"):
            out.append(f'<span class="d-add">{esc}</span>')
        elif ln.startswith("-"):
            out.append(f'<span class="d-del">{esc}</span>')
        else:
            out.append(esc)
    return "\n".join(out)


# ----------------------------------------------------------------------------- sections

_KIND_BADGE = {
    "base": ("base", "b-base"),
    "candidate": ("candidate", "b-cand"),
    "final": ("final", "b-final"),
}


def _kind_span(label: str, kind: str) -> str:
    word, cls = _KIND_BADGE.get(kind, _KIND_BADGE["candidate"])
    return f'<span class="badge {cls}">{_e(label)}</span> <span class="muted">{word}</span>'


def _mode_cell(task: TaskRecord, mode: str) -> str:
    sel = task.selections.get(mode)
    if sel is None:
        return '<td class="muted">—</td>'
    chosen = sel["chosen_label"] or "?"
    kind = "base" if sel["chosen_is_base"] else "final"
    flag = ' <span class="badge b-warn">fallback</span>' if sel["fallback"] else ""
    j, jb = task.judged.get(mode), task.judged_base.get(mode)
    return (
        f"<td>{_kind_span(chosen, kind)}{flag}"
        f"<br><span class='muted'>{_pct(j and j['frac'])} vs base {_pct(jb and jb['frac'])} "
        f"· {_delta(j and j['frac'], jb and jb['frac'])}</span></td>"
    )


def _rollout_details(roll: Rollout) -> str:
    flags = ""
    if roll.dim:
        flags += f' <span class="badge b-dim">{_e(roll.dim)}</span>'
    if roll.fell_back:
        flags += ' <span class="badge b-warn">fell back</span>'
    if roll.kept_base:
        flags += ' <span class="badge b-warn">kept base</span>'
    head = f"{_kind_span(roll.label, roll.kind)}{flags}"
    body = ""
    if roll.notes:
        body += f"<h4>Proposer notes</h4><pre>{_e(roll.notes)}</pre>"
    if roll.harness_diff:
        body += f"<h4>Harness diff</h4><pre>{_diff_html(roll.harness_diff)}</pre>"
    if roll.deliverable:
        body += f"<h4>Deliverable</h4><pre>{_e(roll.deliverable)}</pre>"
    if roll.trajectory:
        body += (
            f"<h4>Trajectory ({_e(roll.trajectory_name)})</h4>"
            f"<pre>{_traj_html(roll.trajectory)}</pre>"
        )
    if not body:
        body = '<p class="muted">No evidence files found for this rollout.</p>'
    return (
        f'<details class="roll"><summary>{head}</summary>'
        f'<div class="body">{body}</div></details>'
    )


def _task_section(task: TaskRecord) -> str:
    status_cls = "b-final" if task.status == "ok" else "b-warn"
    title = f'<span class="badge {status_cls}">{_e(task.status or "?")}</span> {_e(task.task_id)}'
    sel_blocks = ""
    for mode, sel in sorted(task.selections.items()):
        if sel is None:
            continue
        sel_blocks += (
            f"<h4>Blind selection · {_e(mode)}</h4>"
            f"<p>Chose {_kind_span(sel['chosen_label'], 'base' if sel['chosen_is_base'] else 'final')} "
            f"— <i>{_e(sel['why'] or sel['error'] or 'no reason recorded')}</i></p>"
        )
    experience = ""
    if task.experience:
        blocks = "".join(f"<pre>{_e(e)}</pre>" for e in task.experience)
        experience = f"<h4>Experience</h4>{blocks}"
    rollouts = "".join(_rollout_details(r) for r in task.rollouts)
    return (
        f"<details><summary>{title}</summary><div class='body'>"
        f"<p class='muted'>namespace {_e(task.namespace)} · domain {_e(task.domain)} · k={task.k}</p>"
        f"{sel_blocks}{experience}<h4>Rollouts · {len(task.rollouts)}</h4>{rollouts}"
        "</div></details>"
    )


def _domain_table(tasks: Iterable[TaskRecord], mode: str) -> str:
    """One domain's table for one mode, with its score chart when judged."""
    tasks = sorted(tasks, key=lambda t: t.task_id)
    head = "<tr><th>task</th><th>status</th><th>pick · score vs base</th></tr>"
    rows = ""
    for t in tasks:
        rows += (
            f"<tr><td><code>{_e(t.task_id)}</code></td><td>{_e(t.status)}</td>"
            f"{_mode_cell(t, mode)}</tr>"
        )
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
            f"<p>{_e(mode)}: refined <b>{_pct(mean)}</b> vs base {_pct(mean_b)} mean over "
            f"{len(judged_pairs)} judged task(s) — {_delta(mean, mean_b)} pts</p>"
        )
    chart_block = (
        "<figure style='margin:0'>"
        f"{chart}<figcaption>per-task scores, Δ in points under each pair</figcaption>"
        "</figure>"
        if chart
        else ""
    )
    return f'<div class="card">{chart_block}<table>{head}{rows}</table>{gains}</div>'


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


def _header_chips(tree: RunTree) -> str:
    """Top-of-page stat chips: totals and the overall base→refined movement."""
    pairs = [
        (t.judged["agg"]["frac"], t.judged_base["agg"]["frac"])
        for t in tree.tasks
        if isinstance(t.judged.get("agg"), dict)
        and isinstance(t.judged_base.get("agg"), dict)
        and isinstance(t.judged["agg"].get("frac"), (int, float))
        and isinstance(t.judged_base["agg"].get("frac"), (int, float))
    ]
    chosen_final = sum(
        1 for t in tree.tasks for s in t.selections.values() if s and not s["chosen_is_base"]
    )
    kept_base = sum(
        1 for t in tree.tasks for s in t.selections.values() if s and s["chosen_is_base"]
    )
    chips = [
        (str(len(tree.tasks)), "tasks"),
        (str(len(tree.namespaces())), "namespaces"),
        (str(chosen_final), "refined adopted"),
        (str(kept_base), "base kept"),
    ]
    if pairs:
        mean_r = sum(a for a, _ in pairs) / len(pairs)
        mean_b = sum(b for _, b in pairs) / len(pairs)
        gain = mean_r - mean_b
        cls = "up" if gain >= 0 else "down"
        chips += [
            (_pct(mean_b), "mean base score"),
            (_pct(mean_r), "mean refined score"),
            (f'<span class="{cls}">{gain * 100:+.1f} pts</span>', "mean gain"),
        ]
    return (
        '<div class="chips">'
        + "".join(
            f'<div class="chip"><div class="v">{val}</div>'
            f'<div class="l">{_e(lab)}</div></div>'
            for val, lab in chips
        )
        + "</div>"
    )


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
            parts.append(
                '<div class="card"><h3 style="margin-top:0">'
                "mean gain per domain · parallel mode</h3>"
                f"{gain_chart}</div>"
            )
        by_domain: dict[str, list[TaskRecord]] = defaultdict(list)
        for t in tasks:
            by_domain[t.domain or "(none)"].append(t)
        modes = sorted({m for t in tasks for m in t.selections}, key=lambda m: ("agg" != m, m))
        for domain in sorted(by_domain):
            domain_tasks = sorted(by_domain[domain], key=lambda x: x.task_id)
            for mode in modes:
                parts.append(f"<h3>domain · {_e(domain)} · {_e(mode)}</h3>")
                parts.append(_domain_table(domain_tasks, mode))
            parts.extend(_task_section(t) for t in domain_tasks)
        sections += "".join(parts)
    if not tree.tasks:
        sections = '<div class="card"><p>No summary.json found under this runs root.</p></div>'
    stamp = time.strftime("%Y-%m-%d %H:%M")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Harness² runs report</title>"
        f"<style>{_CSS}</style></head><body><div class='page'>"
        f"<header><h1>Harness² runs report</h1>"
        f"<p class='muted'>{_e(tree.root)} · generated {stamp}</p></header>"
        f"{_header_chips(tree)}"
        f"{sections}"
        f"<footer>read-only view of {len(tree.tasks)} task record(s) · "
        "self-contained HTML, no scripts, no external assets</footer>"
        "</div></body></html>"
    )


def write(tree: RunTree, out) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(tree), encoding="utf-8")
    return out


__all__ = ["render", "write"]
