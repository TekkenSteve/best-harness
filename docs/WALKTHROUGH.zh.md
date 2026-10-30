# Harness² 技术走读：数据流、打分协议与防泄漏设计

**版本**：v1 · 2026-10 · 基于本仓库源码逐行核对
**读者**：需要理解（或审计）本系统测量口径的工程师
**实例数据**：codex 0.144.6 × kimi k3 · JobBench `human_resources_specialists/task1` · k=1 · 并行模式

---

## 摘要

Harness² 在模型权重冻结的前提下，将 agent 的外围配置（harness）作为实验变量执行受控对照实验：同一任务、多棵候选 harness 树、行为级证据对比、匿名盲选、事后评审。本文给出系统的形式化数据流、打分协议的精确定义，以及防泄漏设计的逐信道论证。**核心结论：分数在优化回路中不存在；LLM 评审仅做逐条二元判定，计分是确定性的程序逻辑。**

## 1. 术语与记号

| 记号 | 定义 |
|---|---|
| `t` | 任务：指令 + 素材（参考文件），评分细则对执行侧不可见 |
| `h` | harness 树：磁盘上的一组可编辑文件（c1..c8 组件），`H0` 为基线 |
| `R(t, h)` | 一次 rollout：在任务 `t` 上以 harness `h` 执行，产出交付物 `D` 与轨迹 `τ` |
| `E` | 证据集：`{τ, D, diff(h, H0)}`——改进器可见的全部信息 |
| `J(D)` | 评审函数：对交付物 `D` 按 rubric 打分（§5） |
| `k` | 递归预算：基线执行次数 = 候选对数 = 决赛组合数 |

三个逻辑角色（可同模型、逻辑上隔离）：

| 角色 | 职责 | 输入边界 |
|---|---|---|
| **Solver** | 执行任务产出交付物 | `t` + `h`，无评分细则 |
| **Improver**（proposer/composer/selector） | 提案、组合、盲选 | 仅 `E`，**无分数、无 rubric** |
| **Judge** | 对选中交付物打分 | `D` + rubric，选后进场 |

## 2. 设计不变量

以下不变量是全系统正确性的根基，均有强制机制（非约定）：

- **I1 权重冻结**：所有阶段模型权重不变，唯一自变量是 harness。
- **I2 单一变量**：Probe 阶段同任务、同素材、同执行器，仅 `h` 不同——行为差异可归因于配置。
- **I3 无分数回路**：`core/judge.py` 不被方法侧任何模块引用（模块 docstring 明示）；`BenchPrompts` 拒绝含 `score|rubric|grader` 字样的字段（[spec.py:121](../harness2/core/spec.py#L121)，构造期抛错）。
- **I4 盲选匿名性**：候选 → A/B 映射由 `sha1(task_id|k|mode)` 派生种子洗牌（[selection.py:71](../harness2/core/selection.py#L71)），selector 输入不含 provenance。
- **I5 失败非零**：评审失败记 `None` 并从分母剔除（[judge.py:17](../harness2/core/judge.py#L17)），永不折算为 0。
- **I6 仪器固定**：judge 模型身份写入 run identity（[cli/judge.py:170](../harness2/cli/judge.py#L170)），变更即构成新的测量序列。

## 3. 架构分层

```
┌───────────────────────────────────────────────────────────────┐
│ 方法层（method）   recursion.py · prompts.py · selection.py        │
│                    library.py · ledger.py    —— 与执行器无关         │
├───────────────────────────────────────────────────────────────┤
│ 适配层（benchmark） BenchSpec + BenchmarkAdapter                   │
│                    tasks / execute / deliverable / score          │
├───────────────────────────────────────────────────────────────┤
│ 执行层（substrate） Substrate 协议 5 方法                          │
│                    oc (OpenCode) · cx (Codex) · dsh (DSH)         │
│                    —— registry.py 一处注册                         │
└───────────────────────────────────────────────────────────────┘
```

三根轴正交解耦（[spec.py:15](../harness2/core/spec.py#L15) 的模块注释）：method × substrate × benchmark，N 个 bench × M 个执行器无需乘法工作量。

## 4. 数据流协议

逐阶段给出：输入 → 输出 / 关键机制 / 实现位置。

```
            任务 t（剥除评分细则）
                  │
   ① BASE      R(t, H0) × k ──────────────────────────────┐
                  │                                        │ 证据 E
   ② PROPOSE    improver(E) → 2k 棵候选树 {inv1..k, dom1..k}│
                  │                                        │
   ③ PROBE      R(t, hi) × 2k 候选逐一试跑                  │
                  │                                        │
   ④ COMPOSE    composer(全部候选 diff + 行为对比 + notes)   │
                → k 棵决赛树 {agg1..k}                      │
                  │                                        │
   ⑤ FINAL      R(t, aggi) × k 决赛执行                     │
                  │                                        │
   ⑥ SELECT     selector(匿名交付物 A/B/…) → choice + why    │
                  │                                        │
   ⑦ REFLECT    采纳树 + 对比 → cue/lesson/evidence          │
                → 领域经验库（供同域后续任务的 ② 读取） ◀────────┘
   ════════════ 分数边界：以上无任何分数 ════════════
   ⑧ JUDGE     J(D_chosen)（独立命令 harness2-judge）
               --bases 时另有 J(D_base) × k
```

### ④ 的两个模式（成本模型一致）

| | 并行 `agg` | 顺序 `seq` |
|---|---|---|
| 结构 | 2k 候选出自同一基线证据，k 次独立组合 | k 步嵌套，每步在上一步的采纳树上提案 |
| 候选标签 | `inv{i}, dom{i}` → `agg{i}` | 第 t 步：`inv{t}, dom{t}` → `c{t}` |
| 探索/利用 | 宽度优先（candidate 多样性） | 深度优先（携带累积证据逐层精炼） |

**每任务成本（两种模式相同）**：rollouts R = 4k（k 基线 + 2k 探测 + k 决赛）；improver 会话 P = 3k（2k 提案 + k 组合）+ 1 盲选 + 1 反思。成本随 k 线性，选择器开销恒定。

### ⑥ 盲选的池构造

选择池**恰好**是 k 棵决赛树的交付物（[selection.py:93](../harness2/core/selection.py#L93)）：基线不在池中（设计上无弃赛项）；未交付（`!ok || !delivered`）的候选直接丢弃并记录原因。匿名 ID 为 A..Z, AA…，洗牌种子由任务身份派生——重跑一致，跨任务无规律。

## 5. 打分协议（JobBench）

### 5.1 数据结构

任务自带 `RUBRICS.json`：rubric 列表 `{(r_i, w_i, C_i)}`，其中 `C_i` 为子判据集合。实例：8 条 rubric，`Σw_i = 64`，每条 rubric 含 2~4 条 criterion。

### 5.2 判定与计分

对每条 rubric 独立调用一次评审 LLM（[judge.py:482](../dependencies/job-bench-eval/eval/judge.py#L482) 的 prompt 明示：*rubric passes ONLY if ALL criteria pass*），返回每 criterion 的 PASS/FAIL + reasoning + evidence。计分是纯程序逻辑（[judge.py:590](../dependencies/job-bench-eval/eval/judge.py#L590)）：

```
pass(r_i)  :=  ∧_{c ∈ C_i} judge(c)              # 全过才算过
score      :=  Σ_i w_i · 1[pass(r_i)]
frac       :=  score / Σ_i w_i                    # 全有或全无
```

```
打分漏斗（base1 实例，Σw = 64）：

  8 rubric ── LLM 逐条 ──▶ 每条内 2~4 criterion 逐一 PASS/FAIL
      │                        │
      │                   全过？─否─▶ 0
      │                   │是
      │                   ▼ 得 w_i
      ▼
  score = 44   frac = 68.8%        （agg1：score = 58，frac = 90.6%）
```

LLM 输出的是**判断**，加权汇总是**代码**——同一评审模型下测量可复现；评审推理档位被压至 `low`（[config.py:154](../harness2/config.py#L154)），防止思考型评审耗完输出预算导致空 verdict。

### 5.3 输入侧：交付物抽取

评审不读二进制原件。适配器以确定性抽取器（pandas/openpyxl/python-docx/mammoth/pdfplumber）将交付物渲染为文本（[jobbench.py:882](../harness2/benchmarks/jobbench.py#L882)），实例中的 `.xlsx` / `.docx` 即经此通道。抽取失败走原始文本兜底并打印降级提示。

### 5.4 三 benchmark 的评审形态

| Benchmark | 判定者 | 计分 | 输出 |
|---|---|---|---|
| JobBench | LLM（逐 rubric） | 全过→w_i，否则 0 | 加权分 |
| LAB | LLM（上游 run_eval） | 逐 criterion | Partial + All-pass |
| WB code | 容器 verifier | 跑测试 | `tests_passed/total`（无 LLM） |
| WB web/office | 容器 verifier（web 含 LLM） | rubric/LLM | `verifier/score.json` |

## 6. 防泄漏：逐信道论证

| 泄漏信道 | 机制 |
|---|---|
| 任务文本夹带评分细则 | 适配器读取边界剥除（JB `task_prompt`；LAB `_task_json` pop `criteria`） |
| 提示词字段提及测量 | `BenchPrompts.__post_init__` 构造期拒绝（I3） |
| Selector 看到配置来源 | 匿名化 + 池内无基线（I4） |
| 优化回路读到分数 | judge 模块与方法层零引用（I3） |
| Agent 上网搜到公开 rubric | 文档要求在出口层封锁 HF/GitHub（[EXPERIMENTS.md](../docs/EXPERIMENTS.md)）；WB 镜像烘焙 rg 且仅放行 proxy |
| Verifier 产物混入改进器视野 | `wb_render.py` 扫描 verifier-authored 文件并剔除 |
| 评审故障污染分数 | I5：记 `None`、隔离 `.judge_failed`、可续评 |

## 7. 边界与权衡

- **测量即仪器**：frac 的完整含义是"该 judge 模型在该 rubric 上的判定"。跨实验比较必须固定 judge（I6）；更换 judge 需重评全量。
- **全有或全无的代价**：rubric 级 `∧` 语义使分数对部分正确不敏感——criterion 级细节在 `*_judge.json` 的 `criteria_results` 中保留，用于诊断而非计分。
- **单任务样本量**：k=1 的 +21.9pts 是单点证据；域级结论需要 `--max-tasks` 扩样后的均值与方差。
- **基线不在选择池**：设计保证"总有采纳"，代价是 selector 无法表达"全部不如基线"——该信号由 ⑧ 的 base 对比分在报告层呈现。

## 附录 A. 产物 Schema

```
runs/jb/
├── adapt/<ns>/                     ns = <bench>-<substrate>-<mode><k>
│   ├── config.json                 运行身份（三角色模型、端点、seed）
│   ├── judged.json                 选中交付物得分（Scored 行）
│   ├── base_judged.json            基线得分（同构 Scored 行）
│   └── <domain>/<task>/
│       ├── summary.json            ①-⑥ 全过程账本
│       ├── selection/<mode><k>/    匿名候选 A.md/B.md + selection.json
│       ├── reflection/…            ⑦ 经验、adoption.json、harness.diff
│       └── harnesses/<label>/      ②③④ 每棵 harness 树的落盘
└── dataset/<domain>/<task>/
    ├── task_folder/                任务输入
    ├── model_output/<label>/       每次执行的交付物
    └── model_traj/<label>/         每次执行的事件流（JSONL）
```

## 附录 B. 关键代码索引

| 主题 | 位置 |
|---|---|
| 并行/顺序递归 | [core/recursion.py](../harness2/core/recursion.py) `run_parallel:343` / `run_sequential:452` |
| 盲选 | [core/selection.py](../harness2/core/selection.py) `assign_ids:71` / `selection_pool:93` |
| 评审编排 | [core/judge.py](../harness2/core/judge.py) · [cli/judge.py](../harness2/cli/judge.py) |
| 打分判定 | [dependencies/job-bench-eval/eval/judge.py](../dependencies/job-bench-eval/eval/judge.py) `:482` prompt / `:590` 计分 |
| 可编辑面校验 | [core/tree.py](../harness2/core/tree.py) `validate` |
| 协议定义 | [core/spec.py](../harness2/core/spec.py) `BenchmarkAdapter:201` · [substrate/base.py](../harness2/substrate/base.py) `Substrate:48` |
