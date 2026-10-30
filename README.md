# best-harness

> A **scenario-fit evaluation workbench for open-source agent harnesses**, built on
> [Google Harness²](https://github.com/google-research/harness2) — answer "which
> open-source agent framework fits *my* workload best?", and get your harness
> configuration auto-improved in the same run.

English · **[中文](README.zh.md)**

<p>
  <a href="https://arxiv.org/pdf/2610.14224"><img src="https://img.shields.io/badge/Paper-arXiv-EA4335?logo=arxiv&logoColor=white" alt="Paper"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-34A853" alt="License"></a>
</p>

<p>
  <img src="assets/scenario-fit.svg" alt="One run, two answers: which framework fits your scenario, and how the config can still improve" width="900">
</p>

## What this is

OpenCode, Codex, DeepSeek Harness… the list of open-source agent frameworks keeps
growing, but "which one fits *my* scenario" has never had a quantitative answer —
public leaderboards measure someone else's workload, not yours.

best-harness does three things:

1. **Comparable head-to-head**: multiple agent frameworks run the **same task set**
   under the **same judge**, compared domain by domain — that is what scenario fit
   actually means;
2. **Automatic optimization suggestions**: the Harness² Propose–Probe–Compose loop
   refines the harness (prompt, guardrails, memory, skills, scripts) on every task,
   leaving a reviewable diff and behavioral evidence for each change;
3. **Visual reporting**: `harness2-view` renders a runs directory into a
   self-contained HTML report — per-domain gain charts, blind-selection provenance,
   and replayable trajectories.

## Key mechanisms

### A harness is the editable configuration around the model

Model weights never change. What gets tuned are the eight component groups around
the model: **system prompt, guardrails, long-term memory, tool guidance, subagents,
skills, scripts, plugins**. Every proposed candidate is a real tree of files on
disk — reviewable and diffable.

### Propose–Probe–Compose (PPC)

```
Same task: run the base harness ──▶ the improver reads trajectories and proposes
candidates (inv = general, dom = domain-specific)
──▶ every candidate is probed on the SAME task (harness is the only variable)
──▶ the composer picks and merges from observed behavioral contrasts ──▶ final harness
```

**No score ever enters the optimization loop** — the improver sees behavioral
evidence only (trajectories, deliverables, diffs) and never the grading criteria,
so the method cannot overfit the judge by construction.

### Blind selection, scoring only after the pick

Final deliverables are anonymized as A/B (the mapping is shuffled by a task-hash
seed); the selector sees content, not provenance. Only after the pick does the LLM
judge grade it, rubric by rubric. A failed judge is recorded as *ungraded*, never
as zero. The full judging funnel and data-flow walkthrough (Chinese) live in
[docs/WALKTHROUGH.zh.md](docs/WALKTHROUGH.zh.md).

### Per-domain gains: scenario fit, quantified

<p>
  <img src="assets/report-gain.svg" alt="Real run example: base 68.8% → refined 90.6%, +21.9 pts" width="900">
</p>

The report aggregates mean refined-vs-base gain per domain — which domains benefit
from harness tuning and which are fine as-is, at a glance. Re-run the same task set
under another executor and you have a framework-level comparison.

### Cross-task experience library

Each finished task distills a cue/lesson/evidence entry into a domain-isolated
library, read by the proposer of later tasks in the same domain — the harness keeps
improving along the task stream.

## Executors and benchmarks

| Executor (substrate) | Status | Notes |
|---|---|---|
| OpenCode (`oc`) | upstream | three install modes |
| Codex CLI (`cx`) | upstream | configurable Responses endpoint |
| **DeepSeek Harness (`dsh`)** | **added in this fork** | headless NDJSON trajectories, isolated DSH_HOME, WB container image |

| Benchmark | Tasks | Scoring |
|---|---|---|
| JobBench | professional work (35 domains) | LLM judge over weighted rubrics |
| LAB | legal work (1,143 tasks) | LLM judge per criterion, Partial + All-pass |
| WorkBuddy | office / web / code | container verifier (the code subset is a hard test-pass score) |
| your own task set | —— | see "Add your own bench" below |

## Quick start

```bash
# Install (Linux, Python 3.12, uv)
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e '.[render]'

# Credential-free local demo (both recursion modes, deterministic editors)
harness2-demo --output runs/demo --k 2

# Run a real benchmark (JobBench + codex example; set endpoint and key in .env)
harness2-setup jb
harness2-check --bench jb --substrate cx
harness2 --bench jb --substrate cx --k 1 --mode parallel --max-tasks 1
harness2-judge --bench jb --substrate cx --k 1 --mode parallel
harness2-judge --bench jb --substrate cx --k 1 --mode parallel --bases   # base scores for the gain chart

# Render the visual report (Chinese UI by default; --lang en for English)
harness2-view --runs runs -o runs/report.html
```

Model roles are configured independently in `.env`: `HARNESS2_SOLVER_MODEL` /
`HARNESS2_CODEX_*` (executor), `HARNESS2_IMPROVER_MODEL` (improver), and
`HARNESS2_JUDGE_MODEL` (judge, any OpenAI-compatible endpoint). See
[docs/SETUP.md](docs/SETUP.md).

## Add your own bench

A benchmark is one Python class implementing the `BenchmarkAdapter` protocol
([harness2/core/spec.py](harness2/core/spec.py)) — five questions to answer:

| Question | Methods |
|---|---|
| Where tasks come from, how they group | `tasks()` / `domain_of()` / `task_prompt()` (criteria stripped) / `task_files()` |
| How to run one rollout | `execute()` (delegates to the substrate) + a `BenchSpec` (install mode, leak globs) |
| What counts as delivered | `delivered()` / `deliverable_text()` / `trajectory()` (shared `[ACTION]/[OBSERVATION]` grammar) |
| What the baseline is | `base_harness()` + `validate_tree()` |
| How to score | `score()` (called only after selection; a judge failure returns `None`, never 0) |

Then register it in `make_adapter` in [harness2/cli/_common.py](harness2/cli/_common.py).
Reference implementations: [jobbench.py](harness2/benchmarks/jobbench.py),
[lab.py](harness2/benchmarks/lab.py).

## Add your own agent framework

A substrate implements five methods (`surface / materialise / execute /
read_trajectory / preflight`, see [harness2/substrate/base.py](harness2/substrate/base.py))
and registers one line in [harness2/substrate/registry.py](harness2/substrate/registry.py) —
CLI choices, adapter construction and help text all derive from it. See the
fork-added [dsh.py](harness2/substrate/dsh.py) for a complete example.

## Docs

| Doc | Contents |
|---|---|
| [docs/WALKTHROUGH.zh.md](docs/WALKTHROUGH.zh.md) | **Full data-flow and judging walkthrough (Chinese, with diagrams)** |
| [docs/SETUP.md](docs/SETUP.md) | Install, models and credentials |
| [docs/BENCHMARKS.md](docs/BENCHMARKS.md) | Per-benchmark data, domains and grading |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Recursion budgets, task selection, reproducibility, leak control |

## Acknowledgements and license

This project is a derivative of Google Research's
[Harness²](https://github.com/google-research/harness2) (Apache-2.0). The upstream
method and paper:

```bibtex
@article{xu2026harness2,
  title   = {Harness$^2$: Recursive Agent Harnessing for an Open World},
  author  = {Xu, Ruiyao and Chen, Yanfei and CuiZhu, Zhongying and Dalvi Mishra, Bhavana and Ming, Yifei and Yu, Han and Han, Rujun and Lee, Chen-Yu and Pfister, Tomas},
  journal = {arXiv preprint arXiv:2610.14224},
  year    = {2026}
}
```

Code in this repository is released under [Apache-2.0](LICENSE); third-party code
under `third_party/`, benchmarks, and datasets retain their upstream licenses (see
[dependencies/README.md](dependencies/README.md)).
