"""Run one LAB task through dsh headless.

    uv run python dsh/run_dsh_lab.py \
        --task corporate-ma/review-data-room-red-flag-review \
        --dsh-home /staged/home

Mirrors `opencode/run_opencode.py` — same task loading, same prompt shape, same
results/<run_id>/ layout (workspace/documents read-only, workspace/output,
metrics.json), so `run_eval` scores a dsh run unchanged. What differs is the loop:
dsh owns it with its own tools, on the host, one `dsh --profile headless` process
per rollout under an isolated DSH_HOME staged by the harness2 adapter. The nudge
loop resumes the persisted session by id (`--session-id`), the opencode runner's
`--session` equivalent.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from harness.run import BENCH_ROOT, _load_env, load_task
from utils.stdio import force_utf8_stdio

DEFAULT_DSH_BIN = os.environ.get("DSH_BIN", "dsh")
DEFAULT_PERMISSION_MODE = os.environ.get("DSH_PERMISSION_MODE", "workspace-write")

NUDGE = (
    "Continue the assignment to completion now. Do not ask for confirmation. "
    "If every deliverable has already been written to the output directory, "
    "reply with exactly: DONE"
)

# dsh's --json writer bounds every event itself (8 KiB strings / 32 KiB lines).
SESSION_STREAM = "dsh_session.jsonl"


def build_prompt(task: dict, workspace: Path) -> str:
    """The assignment message: the task instructions verbatim plus the concrete paths."""
    return (
        f"=== WORKSPACE ===\n{workspace}\n\n"
        f"=== DOCUMENTS (read-only) ===\n{workspace / 'documents'}\n\n"
        f"=== OUTPUT DIRECTORY (deliverables go here) ===\n{workspace / 'output'}\n\n"
        f"=== ASSIGNMENT ===\n{task['instructions']}"
    )


def parse_events(session_path: Path) -> list[dict]:
    """The NDJSON run-event stream, junk tolerated, torn last line dropped."""
    events: list[dict] = []
    try:
        raw = session_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events
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


def summarize(events: list[dict]) -> dict:
    """The metrics a dsh stream carries: tokens per committed step, calls, final text."""
    tool_calls = 0
    in_tok = out_tok = 0
    session_id = ""
    final_text = ""
    finished_cleanly = False
    for ev in events:
        et = ev.get("type")
        if et == "session":
            session_id = str(ev.get("sessionId") or session_id)
        elif et == "tool_call":
            tool_calls += 1
        elif et == "status" and ev.get("phase") == "step_end":
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            in_tok += int(usage.get("inputTokens") or 0)
            out_tok += int(usage.get("outputTokens") or 0)
        elif et == "final":
            reason = ev.get("reason")
            finished_cleanly = not (isinstance(reason, dict) and reason.get("kind") == "error")
            txt = ev.get("text")
            final_text = txt if isinstance(txt, str) else ""
    steps = sum(
        1 for ev in events if ev.get("type") == "status" and ev.get("phase") == "step_end"
    )
    return {
        "session_id": session_id,
        "steps": steps,
        "tool_calls": tool_calls,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "final_text": final_text,
        "finished_cleanly": finished_cleanly,
    }


def doc_coverage(events: list[dict], docs_dir: Path) -> tuple[int, int]:
    """Best-effort read coverage: a document counts as read when its name appears in
    any tool-call input (dsh's stream carries parsed arguments, not file handles)."""
    names = [p.name for p in sorted(docs_dir.rglob("*")) if p.is_file()]
    if not names:
        return 0, 0
    blob = "\n".join(
        json.dumps(ev.get("input"), default=str)
        for ev in events
        if ev.get("type") == "tool_call"
    )
    return sum(1 for n in names if n in blob), len(names)


def invoke(dsh_bin: str, task_text: str, *, session_id: str | None, permission_mode: str,
           dsh_home: Path, timeout: int, session_path: Path, log_path: Path) -> int:
    """One headless rollout, appending its NDJSON stream to session_path."""
    argv = [
        "timeout", "--kill-after=30s", str(int(max(30, timeout))),
        dsh_bin, "--profile", "headless", "--json",
        "--permission-mode", permission_mode,
        "--approval-policy", "never",
    ]
    if session_id:
        argv += ["--session-id", session_id]
    argv.append("-")
    with open(session_path, "a", encoding="utf-8") as out, \
         open(log_path, "a", encoding="utf-8") as err:
        proc = subprocess.run(
            argv, input=task_text, text=True, cwd=str(os.getcwd()),
            env={**os.environ, "DSH_HOME": str(dsh_home)},
            stdout=out, stderr=err,
        )
    return proc.returncode


def main(args) -> None:
    force_utf8_stdio()
    _load_env()

    if args.run_id is None:
        model_short = (args.model or "model").split("/")[-1].replace(".", "-")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.run_id = f"{args.task}/dsh-{model_short}/{ts}"

    print(f"Loading task: {args.task}")
    task = load_task(task_name=args.task)

    results_dir = Path(args.results_root or BENCH_ROOT / "results") / args.run_id
    workspace = results_dir / "workspace"
    output_dir = workspace / "output"
    docs_dest = workspace / "documents"
    workspace.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if docs_dest.exists():
        shutil.rmtree(docs_dest)
    shutil.copytree(task["docs_dir"], docs_dest)
    for p in docs_dest.rglob("*"):
        if p.is_file():
            p.chmod(0o444)

    if args.scripts_dir and Path(args.scripts_dir).is_dir():
        dest = workspace / "harness_scripts"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(args.scripts_dir, dest)

    scripts = sorted(p.name for p in (workspace / "harness_scripts").glob("*.py")) \
        if (workspace / "harness_scripts").is_dir() else []
    config = {
        "runner": "dsh",
        "model": args.model,
        "task": args.task,
        "run_id": args.run_id,
        "timeout": args.timeout,
        "permission_mode": args.permission_mode,
        "dsh_home": str(args.dsh_home),
        "scripts": scripts,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (results_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    session_path = results_dir / SESSION_STREAM
    log_path = results_dir / "dsh_stderr.log"

    print(f"Runner:    dsh ({args.dsh_bin})")
    print(f"Model:     {args.model}")
    print(f"DSH_HOME:  {args.dsh_home}")
    print(f"Workspace: {workspace}")
    print(f"Output:    {output_dir}")
    print()

    t0 = time.time()
    rc = invoke(args.dsh_bin, build_prompt(task, workspace), session_id=None,
                permission_mode=args.permission_mode, dsh_home=Path(args.dsh_home),
                timeout=args.timeout, session_path=session_path, log_path=log_path)

    summary = summarize(parse_events(session_path))
    for _ in range(args.max_continue):
        remaining = args.timeout - (time.time() - t0)
        if remaining < 60 or not summary["session_id"]:
            break
        if (any(f.is_file() for f in output_dir.rglob("*"))
                and summary["final_text"].strip() == "DONE"):
            break
        before = summary["tool_calls"]
        rc = invoke(args.dsh_bin, NUDGE, session_id=summary["session_id"],
                    permission_mode=args.permission_mode, dsh_home=Path(args.dsh_home),
                    timeout=remaining, session_path=session_path, log_path=log_path)
        summary = summarize(parse_events(session_path))
        if summary["tool_calls"] == before:
            break

    wall = time.time() - t0

    graded_output = results_dir / "output"
    graded_output.mkdir(parents=True, exist_ok=True)
    for src in output_dir.rglob("*"):
        if src.is_file():
            dest = graded_output / src.relative_to(output_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    docs_read, docs_total = doc_coverage(parse_events(session_path), docs_dest)
    metrics = {
        "runner": "dsh",
        "model": args.model,
        "task": args.task,
        "run_id": args.run_id,
        "turn_count": summary["steps"],
        "input_tokens": summary["input_tokens"],
        "output_tokens": summary["output_tokens"],
        "total_tokens": summary["input_tokens"] + summary["output_tokens"],
        "wall_clock_seconds": round(wall, 1),
        "cost_usd": 0.0,
        "finished_cleanly": summary["finished_cleanly"] and rc == 0,
        "exit_code": rc,
        "tool_calls": summary["tool_calls"],
        "documents_read": docs_read,
        "total_documents": docs_total,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (results_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    delivered = sorted(p.name for p in graded_output.rglob("*") if p.is_file())
    print()
    print("=" * 60)
    print(f"Run complete: {args.run_id}")
    print(f"  Steps:          {metrics['turn_count']}")
    print(f"  Tool calls:     {metrics['tool_calls']}")
    print(f"  Input tokens:   {metrics['input_tokens']:,}")
    print(f"  Output tokens:  {metrics['output_tokens']:,}")
    print(f"  Wall clock:     {metrics['wall_clock_seconds']}s")
    print(f"  Docs read:      {docs_read}/{docs_total}")
    print(f"  Deliverables:   {', '.join(delivered) or 'NONE'}")
    print(f"  Exit code:      {rc}")
    print(f"\nResults saved to: {results_dir}")


parser = argparse.ArgumentParser(description="Run a LAB task through dsh headless")
parser.add_argument("--results-root", help="Directory for run artifacts")
parser.add_argument("--model", default="", help="Model id, recorded in config/metrics")
parser.add_argument("--task", required=True,
                    help="Task ID (e.g., corporate-ma/review-data-room-red-flag-review)")
parser.add_argument("--run-id", default=None, help="Run identifier (auto-generated if omitted)")
parser.add_argument("--dsh-home", required=True,
                    help="Isolated DSH_HOME staged by the harness2 adapter (never ~/.dsh)")
parser.add_argument("--scripts-dir", default=None,
                    help="Staged harness scripts, delivered to <workspace>/harness_scripts")
parser.add_argument("--timeout", type=int, default=3600, help="Wall-clock budget in seconds")
parser.add_argument("--max-continue", type=int, default=2,
                    help="Nudge resumes of the persisted session if the agent stops early")
parser.add_argument("--permission-mode", default=DEFAULT_PERMISSION_MODE,
                    help="dsh permission preset for the rollout (default: %(default)s)")
parser.add_argument("--dsh-bin", default=DEFAULT_DSH_BIN, help="dsh executable")


if __name__ == "__main__":
    main(parser.parse_args())
