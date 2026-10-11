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
describes. UI text is bilingual (``--lang zh`` default, ``--lang en``); data
content is always shown verbatim.
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
.chip .l { font-size: .72rem; color: var(--muted); margin-top: .15rem; }
table { border-collapse: separate; border-spacing: 0; width: 100%;
        margin: .6rem 0 1rem; font-size: .92rem; }
th, td { padding: .5rem .7rem; text-align: left; vertical-align: top;
         border-bottom: 1px solid var(--border); }
th { font-size: .72rem; letter-spacing: .05em; color: var(--muted);
     border-bottom: 1.5px solid var(--border); }
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
details.guide { border-left: 3px solid var(--accent); }
details.guide > summary { font-weight: 600; }
details.guide .body p, details.guide .body li { margin: .35rem 0; }
details.guide .body ul { padding-left: 1.2rem; margin: .3rem 0 .6rem; }
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

# ----------------------------------------------------------------------------- i18n

LANGS = ("zh", "en")

_T = {
    "zh": {
        "page_title": "Harness² 运行报告",
        "header_meta": "数据目录：{root} · 生成于 {stamp}",
        "chip_tasks": "任务数",
        "chip_namespaces": "实验组",
        "chip_adopted": "采纳了优化配置",
        "chip_kept_base": "保留了基线",
        "chip_base": "基线平均分",
        "chip_refined": "优化后平均分",
        "chip_gain": "平均提升",
        "ns_header": "{ns} · {n} 个任务",
        "gain_title": "各领域平均提升 · 并行模式",
        "domain_mode": "领域：{domain} · 模式：{mode}",
        "col_task": "任务",
        "col_status": "状态",
        "col_pick": "盲选采纳 · 得分对比基线",
        "gains_line": "{mode}：优化后 <b>{mean}</b> vs 基线 {mean_b}（{n} 个已评分任务的均值）—— Δ {delta} 个百分点",
        "chart_caption": "每对柱子是一次任务：灰 = 基线配置，绿/红 = 优化后配置（绿涨红跌）；柱下数字是分差（百分点）",
        "word_base": "基线",
        "word_candidate": "候选",
        "word_final": "最终",
        "badge_fallback": "回退",
        "badge_fell_back": "回退",
        "badge_kept_base": "保留基线",
        "task_meta": "实验组 {ns} · 领域 {domain} · 每任务尝试 k={k}",
        "blind_title": "盲选 · {mode}",
        "blind_line": "采纳了 {label} —— 理由：{why}",
        "no_reason": "未记录理由",
        "h_notes": "改进器的提案说明",
        "h_diff": "配置改动（harness diff）",
        "h_deliverable": "交付物",
        "h_trajectory": "执行轨迹（{name}）",
        "h_experience": "沉淀的经验",
        "h_rollouts": "执行记录 · {n}",
        "no_evidence": "未找到这次执行的证据文件。",
        "empty": "该目录下没有找到 summary.json。",
        "footer": "只读视图 · 共 {n} 条任务记录 · 单文件 HTML，无脚本、无外部资源",
        "guide_title": "📖 怎么看这份报告",
    },
    "en": {
        "page_title": "Harness² runs report",
        "header_meta": "{root} · generated {stamp}",
        "chip_tasks": "tasks",
        "chip_namespaces": "namespaces",
        "chip_adopted": "refined adopted",
        "chip_kept_base": "base kept",
        "chip_base": "mean base score",
        "chip_refined": "mean refined score",
        "chip_gain": "mean gain",
        "ns_header": "{ns} · {n} task(s)",
        "gain_title": "mean gain per domain · parallel mode",
        "domain_mode": "domain: {domain} · mode: {mode}",
        "col_task": "task",
        "col_status": "status",
        "col_pick": "blind pick · score vs base",
        "gains_line": "{mode}: refined <b>{mean}</b> vs base {mean_b}, mean over {n} judged task(s) — Δ {delta} pts",
        "chart_caption": "each pair of bars is one task: grey = base harness, green/red = refined; the number below is the delta in points",
        "word_base": "base",
        "word_candidate": "candidate",
        "word_final": "final",
        "badge_fallback": "fallback",
        "badge_fell_back": "fell back",
        "badge_kept_base": "kept base",
        "task_meta": "namespace {ns} · domain {domain} · k={k} attempts per task",
        "blind_title": "Blind selection · {mode}",
        "blind_line": "chose {label} — {why}",
        "no_reason": "no reason recorded",
        "h_notes": "Proposer notes",
        "h_diff": "Harness diff",
        "h_deliverable": "Deliverable",
        "h_trajectory": "Trajectory ({name})",
        "h_experience": "Experience",
        "h_rollouts": "Rollouts · {n}",
        "no_evidence": "No evidence files found for this rollout.",
        "empty": "No summary.json found under this runs root.",
        "footer": "read-only view of {n} task record(s) · self-contained HTML, no scripts, no external assets",
        "guide_title": "📖 How to read this report",
    },
}

_GUIDE = {
    "zh": """
<p>Harness² 对<b>同一个任务</b>做多轮对照实验，被优化的对象叫 <b>harness</b>——模型周围的一整套
可编辑配置（系统提示词、守卫规则、长期记忆、工具指引、技能、脚本等），模型本身不动。</p>
<p>流程：先用<b>基线配置</b>（base，未改动的原始配置）执行一遍；再由「改进器」提出两类<b>候选配置</b>
——<code>inv</code> 是通用改进，<code>dom</code> 是面向当前领域的定制改进——分别试跑；
然后组合出<b>最终配置</b>（<code>agg</code>）；最后由<b>盲选</b>挑出要采纳的交付物
——评审时看不到每份交付物来自哪个配置，避免偏向。</p>
<ul>
<li><b>徽章</b>：<span class="badge b-base">基线</span> 原始配置的执行；
<span class="badge b-cand">候选</span> 试跑的中间方案；
<span class="badge b-final">最终</span> 组合后被采纳的方案；
<span class="badge b-warn">回退</span> 没有可用候选、只能保留基线。</li>
<li><b>得分</b>：由独立的 LLM 评审按任务的评分细则（rubric）逐条打分。
灰柱 = 基线，绿柱 = 提升，红柱 = 下降，柱下 Δ 是分差（百分点）。</li>
<li><b>执行轨迹</b>：一次执行的完整过程。彩色标签：
<span class="tg tg-action">[ACTION]</span> 工具调用、
<span class="tg tg-obs">[OBSERVATION]</span> 工具返回、
<span class="tg tg-assist">[ASSISTANT]</span> 模型回复、
<span class="tg tg-err">[ERROR]</span> 出错。</li>
<li><b>沉淀的经验</b>：跑完该任务后总结的可复用教训，会带入同领域的后续任务——
这是 harness²「跨任务学习」的那一层。</li>
<li><b>模式</b>：并行（agg）= 多个候选同时试跑后组合；顺序（seq）= 一步步连续改进。</li>
</ul>
""",
    "en": """
<p>Harness² runs a controlled experiment <b>per task</b>. The object being tuned is the
<b>harness</b> — the editable configuration around the model (system prompt, guardrails,
memory, tool guidance, skills, scripts); the model itself is never changed.</p>
<p>Flow: the task runs once under the <b>base</b> harness; the improver then proposes two kinds of
<b>candidates</b> — <code>inv</code> (general edits) and <code>dom</code> (domain-specific edits) —
each probed on the same task; a composer merges them into the <b>final</b> harness
(<code>agg</code>); finally a <b>blind selection</b> picks the deliverable to adopt, without
seeing which harness produced it.</p>
<ul>
<li><b>Badges</b>: <span class="badge b-base">base</span> run with the original harness;
<span class="badge b-cand">candidate</span> intermediate probe;
<span class="badge b-final">final</span> adopted composition;
<span class="badge b-warn">fallback</span> no usable candidate, base kept.</li>
<li><b>Scores</b>: an independent LLM judge grades each task against its rubric, criterion by
criterion. Grey bar = base, green = gain, red = regression; Δ below each pair is in points.</li>
<li><b>Trajectory</b>: the full record of one execution. Colored tags:
<span class="tg tg-action">[ACTION]</span> tool call,
<span class="tg tg-obs">[OBSERVATION]</span> tool result,
<span class="tg tg-assist">[ASSISTANT]</span> model reply,
<span class="tg tg-err">[ERROR]</span> failure.</li>
<li><b>Experience</b>: the lesson distilled after the task, carried into later tasks of the
same domain — the cross-task learning layer.</li>
<li><b>Modes</b>: parallel (agg) = probe several candidates, then compose; sequential (seq) =
successive refinement steps.</li>
</ul>
""",
}

_MODE_LABEL = {
    "zh": {"agg": "并行", "seq": "顺序"},
    "en": {"agg": "parallel", "seq": "sequential"},
}


def _mode_label(mode: str, lang: str) -> str:
    return _MODE_LABEL.get(lang, {}).get(mode, mode)


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

_KIND_BADGE = {"base": "b-base", "candidate": "b-cand", "final": "b-final"}


def _kind_span(label: str, kind: str, t8n: dict) -> str:
    word = {
        "base": t8n["word_base"],
        "candidate": t8n["word_candidate"],
        "final": t8n["word_final"],
    }.get(kind, t8n["word_candidate"])
    cls = _KIND_BADGE.get(kind, "b-cand")
    return f'<span class="badge {cls}">{_e(label)}</span> <span class="muted">{_e(word)}</span>'


def _mode_cell(task: TaskRecord, mode: str, t8n: dict) -> str:
    sel = task.selections.get(mode)
    if sel is None:
        return '<td class="muted">—</td>'
    chosen = sel["chosen_label"] or "?"
    kind = "base" if sel["chosen_is_base"] else "final"
    flag = (
        f' <span class="badge b-warn">{_e(t8n["badge_fallback"])}</span>'
        if sel["fallback"]
        else ""
    )
    j, jb = task.judged.get(mode), task.judged_base.get(mode)
    return (
        f"<td>{_kind_span(chosen, kind, t8n)}{flag}"
        f"<br><span class='muted'>{_pct(j and j['frac'])} vs {_pct(jb and jb['frac'])} "
        f"· {_delta(j and j['frac'], jb and jb['frac'])}</span></td>"
    )


def _rollout_details(roll: Rollout, t8n: dict) -> str:
    flags = ""
    if roll.dim:
        flags += f' <span class="badge b-dim">{_e(roll.dim)}</span>'
    if roll.fell_back:
        flags += f' <span class="badge b-warn">{_e(t8n["badge_fell_back"])}</span>'
    if roll.kept_base:
        flags += f' <span class="badge b-warn">{_e(t8n["badge_kept_base"])}</span>'
    head = f"{_kind_span(roll.label, roll.kind, t8n)}{flags}"
    body = ""
    if roll.notes:
        body += f"<h4>{_e(t8n['h_notes'])}</h4><pre>{_e(roll.notes)}</pre>"
    if roll.harness_diff:
        body += f"<h4>{_e(t8n['h_diff'])}</h4><pre>{_diff_html(roll.harness_diff)}</pre>"
    if roll.deliverable:
        body += f"<h4>{_e(t8n['h_deliverable'])}</h4><pre>{_e(roll.deliverable)}</pre>"
    if roll.trajectory:
        body += (
            f"<h4>{_e(t8n['h_trajectory'].format(name=roll.trajectory_name))}</h4>"
            f"<pre>{_traj_html(roll.trajectory)}</pre>"
        )
    if not body:
        body = f'<p class="muted">{_e(t8n["no_evidence"])}</p>'
    return (
        f'<details class="roll"><summary>{head}</summary>'
        f'<div class="body">{body}</div></details>'
    )


def _task_section(task: TaskRecord, lang: str, t8n: dict) -> str:
    title = (
        f'<span class="badge {"b-final" if task.status == "ok" else "b-warn"}">'
        f"{_e(task.status or '?')}</span> {_e(task.task_id)}"
    )
    sel_blocks = ""
    for mode, sel in sorted(task.selections.items()):
        if sel is None:
            continue
        kind = "base" if sel["chosen_is_base"] else "final"
        before, _, after = t8n["blind_line"].partition("{label}")
        why = sel["why"] or sel["error"] or t8n["no_reason"]
        sel_blocks += (
            f"<h4>{_e(t8n['blind_title'].format(mode=_mode_label(mode, lang)))}</h4>"
            f"<p>{_e(before)}{_kind_span(sel['chosen_label'], kind, t8n)}"
            f"{_e(after.format(why=why))}</p>"
        )
    experience = ""
    if task.experience:
        blocks = "".join(f"<pre>{_e(e)}</pre>" for e in task.experience)
        experience = f"<h4>{_e(t8n['h_experience'])}</h4>{blocks}"
    rollouts = "".join(_rollout_details(r, t8n) for r in task.rollouts)
    return (
        f"<details><summary>{title}</summary><div class='body'>"
        f"<p class='muted'>"
        f"{_e(t8n['task_meta'].format(ns=task.namespace, domain=task.domain, k=task.k))}</p>"
        f"{sel_blocks}{experience}"
        f"<h4>{_e(t8n['h_rollouts'].format(n=len(task.rollouts)))}</h4>{rollouts}"
        "</div></details>"
    )


def _domain_table(tasks: Iterable[TaskRecord], mode: str, lang: str, t8n: dict) -> str:
    """One domain's table for one mode, with its score chart when judged."""
    tasks = sorted(tasks, key=lambda t: t.task_id)
    head = (
        f"<tr><th>{_e(t8n['col_task'])}</th><th>{_e(t8n['col_status'])}</th>"
        f"<th>{_e(t8n['col_pick'])}</th></tr>"
    )
    rows = ""
    for t in tasks:
        rows += (
            f"<tr><td><code>{_e(t.task_id)}</code></td><td>{_e(t.status)}</td>"
            f"{_mode_cell(t, mode, t8n)}</tr>"
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
        gains = "<p>" + t8n["gains_line"].format(
            mode=_e(_mode_label(mode, lang)),
            mean=_pct(mean),
            mean_b=_pct(mean_b),
            n=len(judged_pairs),
            delta=_delta(mean, mean_b),
        ) + "</p>"
    chart_block = (
        "<figure style='margin:0'>"
        f"{chart}<figcaption>{_e(t8n['chart_caption'])}</figcaption>"
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


def _header_chips(tree: RunTree, t8n: dict) -> str:
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
        (str(len(tree.tasks)), t8n["chip_tasks"]),
        (str(len(tree.namespaces())), t8n["chip_namespaces"]),
        (str(chosen_final), t8n["chip_adopted"]),
        (str(kept_base), t8n["chip_kept_base"]),
    ]
    if pairs:
        mean_r = sum(a for a, _ in pairs) / len(pairs)
        mean_b = sum(b for _, b in pairs) / len(pairs)
        gain = mean_r - mean_b
        cls = "delta-up" if gain >= 0 else "delta-down"
        chips += [
            (_pct(mean_b), t8n["chip_base"]),
            (_pct(mean_r), t8n["chip_refined"]),
            (f'<span class="{cls}">{gain * 100:+.1f} pts</span>', t8n["chip_gain"]),
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


def render(tree: RunTree, *, lang: str = "zh") -> str:
    """The whole report as one HTML document string, in `lang` ("zh" or "en")."""
    if lang not in LANGS:
        raise ValueError(f"lang must be one of {LANGS}, got {lang!r}")
    t8n = _T[lang]
    by_ns: dict[str, list[TaskRecord]] = defaultdict(list)
    for t in tree.tasks:
        by_ns[t.namespace].append(t)
    sections = ""
    for ns in sorted(by_ns):
        tasks = by_ns[ns]
        parts = [f"<h2>{_e(t8n['ns_header'].format(ns=ns, n=len(tasks)))}</h2>"]
        gain_chart = _gain_chart(_ns_gain_domains(tasks))
        if gain_chart:
            parts.append(
                f'<div class="card"><h3 style="margin-top:0">{_e(t8n["gain_title"])}</h3>'
                f"{gain_chart}</div>"
            )
        by_domain: dict[str, list[TaskRecord]] = defaultdict(list)
        for t in tasks:
            by_domain[t.domain or "(none)"].append(t)
        modes = sorted({m for t in tasks for m in t.selections}, key=lambda m: ("agg" != m, m))
        for domain in sorted(by_domain):
            domain_tasks = sorted(by_domain[domain], key=lambda x: x.task_id)
            for mode in modes:
                parts.append(
                    f"<h3>{_e(t8n['domain_mode'].format(domain=domain, mode=_mode_label(mode, lang)))}</h3>"
                )
                parts.append(_domain_table(domain_tasks, mode, lang, t8n))
            parts.extend(_task_section(t, lang, t8n) for t in domain_tasks)
        sections += "".join(parts)
    if not tree.tasks:
        sections = f'<div class="card"><p>{_e(t8n["empty"])}</p></div>'
    stamp = time.strftime("%Y-%m-%d %H:%M")
    guide = (
        f'<details class="card guide"><summary>{_e(t8n["guide_title"])}</summary>'
        f'<div class="body">{_GUIDE[lang]}</div></details>'
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_e(t8n['page_title'])}</title>"
        f"<style>{_CSS}</style></head><body><div class='page'>"
        f"<header><h1>{_e(t8n['page_title'])}</h1>"
        f"<p class='muted'>{_e(t8n['header_meta'].format(root=tree.root, stamp=stamp))}</p></header>"
        f"{_header_chips(tree, t8n)}"
        f"{guide}"
        f"{sections}"
        f"<footer>{_e(t8n['footer'].format(n=len(tree.tasks)))}</footer>"
        "</div></body></html>"
    )


def write(tree: RunTree, out, *, lang: str = "zh") -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(tree, lang=lang), encoding="utf-8")
    return out


__all__ = ["LANGS", "render", "write"]
