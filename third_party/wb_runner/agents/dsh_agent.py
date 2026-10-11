"""DshAgent -- a dsh (DeepSeek Harness) BaseInstalledAgent for the external bench:
headless one-shot rollout under an isolated DSH_HOME, NDJSON event stream mapped to ATIF.

Model routing differs from the openai-protocol harnesses: dsh's llm-deepseek provider
speaks the Anthropic Messages protocol against DEEPSEEK_BASE_URL (a Messages-compatible
HTTP(S) root), authenticated with DEEPSEEK_API_KEY. Under model_connection local_proxy
the agent points both at the bench proxy and prefixes the key with the trial id for
per-trial attribution, the same `{trial}::{route}` convention oc_agent uses.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories.agent import Agent
from harbor.models.trajectories.final_metrics import FinalMetrics
from harbor.models.trajectories.metrics import Metrics
from harbor.models.trajectories.step import Step
from harbor.models.trajectories.tool_call import ToolCall
from harbor.models.trajectories.trajectory import Trajectory

from workbuddy_bench.agents._agent_user import ensure_agent_user

_OUTPUT_FILENAME = "dsh-output.jsonl"

# Per-trial isolated home: a fresh container starts empty, but an explicit path keeps
# the rollout off any baked-in ~/.dsh and makes the staging visible in the manifest.
_DSH_HOME_SUBDIR = ".dsh-trial"


class DshAgent(BaseInstalledAgent):
    """dsh headless CLI agent (installed-CLI harness) for harbor."""

    SUPPORTS_ATIF: bool = True

    def __init__(self, logs_dir: Path, *args, **kwargs):
        self._dsh_version: str | None = kwargs.pop("DSH_VERSION", None)
        # dsh headless has no --max-turns; advisory and recorded only, like opencode.
        mt = kwargs.pop("DSH_MAX_TURNS", None)
        self._max_turns: int | None = int(mt) if mt is not None else None
        self._permission_mode = str(kwargs.pop("DSH_PERMISSION_MODE", None) or "workspace-write")
        connection = kwargs.pop("connection", None) or {}
        self._conn_mode = str(connection.get("mode") or "direct")
        self._proxy_url = str(connection.get("proxy_url") or "")
        self._mount_path = str(kwargs.pop("mount_path", None) or "/opt/dsh")
        self._session_id = self._trial_id_from_logs_dir(logs_dir)
        super().__init__(logs_dir, *args, version=self._dsh_version, **kwargs)

    @staticmethod
    def _trial_id_from_logs_dir(logs_dir: Path | None) -> str:
        """The per-trial id, used to prefix the proxy key so proxy logs split by trial."""
        try:
            p = Path(logs_dir)
        except TypeError:
            return ""
        return p.parent.name if p.name == "agent" else ""

    @staticmethod
    def name() -> str:
        return "dsh"

    def get_version_command(self) -> str | None:
        return 'export PATH="/usr/local/bin:$PATH"; dsh --version'

    async def install(self, environment: BaseEnvironment) -> None:
        mount_bin = f"{self._mount_path.rstrip('/')}/bin"
        await self.exec_as_root(
            environment,
            command=(
                'export PATH="/usr/local/bin:$PATH"; '
                f'[ -x "{mount_bin}/dsh" ] && '
                f'ln -sf "{mount_bin}/dsh" "/usr/local/bin/dsh"; '
                f'command -v rg >/dev/null 2>&1 || '
                f'{{ [ -x "{mount_bin}/rg" ] && ln -sf "{mount_bin}/rg" "/usr/local/bin/rg"; }}; '
                "if command -v dsh >/dev/null 2>&1; then "
                "echo 'dsh present (mount image)'; "
                "else "
                f'echo "ERROR: dsh mount not found at {mount_bin}." >&2; '
                'echo "  Build it: scripts/harness/build-harness-mounts.sh '
                '--harness dsh/<version>" >&2; '
                "exit 1; fi && "
                "dsh --version && "
                '{ command -v rg >/dev/null 2>&1 || '
                '{ echo "ERROR: ripgrep (rg) not on PATH." >&2; exit 1; }; } && '
                "rg --version | head -1"
            ),
        )
        await ensure_agent_user(self, environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        instruction = self.render_instruction(instruction)
        escaped_instruction = shlex.quote(instruction)
        route = self.model_name  # route slug under local_proxy; real id under direct

        if self._conn_mode == "local_proxy":
            base_url = self._proxy_url
            api_key = self._get_env("DEEPSEEK_API_KEY") or "dummy-for-proxy"
            if self._session_id:
                api_key = f"{self._session_id}::{route}"
        else:
            base_url = self._get_env("DEEPSEEK_BASE_URL") or ""
            api_key = self._get_env("DEEPSEEK_API_KEY") or ""

        await self._run_dsh(
            environment,
            base_url=base_url,
            api_key=api_key,
            escaped_instruction=escaped_instruction,
        )

    async def _run_dsh(
        self, environment: BaseEnvironment, *, base_url: str, api_key: str,
        escaped_instruction: str,
    ) -> None:
        """Run one headless task, NDJSON stream tee'd to the agent log mount."""
        home_fix = 'export HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"; '
        # dsh reads its user-global AGENTS.md and skills from DSH_HOME; per-trial means
        # the only harness in play is the one this trial staged.
        env_prefix = (
            home_fix
            + f'export DSH_HOME="$HOME/{_DSH_HOME_SUBDIR}"; '
            + f'mkdir -p "$DSH_HOME"; '
            + f'export DEEPSEEK_BASE_URL={shlex.quote(base_url)}; '
            + f'export DEEPSEEK_API_KEY={shlex.quote(api_key)}; '
        )
        output_path = f"/logs/agent/{_OUTPUT_FILENAME}"
        # The task text rides stdin (`-`); --json emits the bounded NDJSON run events;
        # --approval-policy never is what makes an unattended rollout terminate instead
        # of waiting on a permission prompt.
        run_cmd = (
            'export PATH="/usr/local/bin:$PATH"; '
            f"{env_prefix}"
            f"printf %s {escaped_instruction} | "
            f"dsh --profile headless --json "
            f"--permission-mode {shlex.quote(self._permission_mode)} "
            f"--approval-policy never - "
            f"2>&1 | tee {output_path}"
        )
        await self.exec_as_agent(environment, command=run_cmd)

    def populate_context_post_run(self, context: AgentContext) -> None:
        events = self._read_events()
        if events is None:
            return
        trajectory = self._build_trajectory(events)
        if trajectory:
            traj_path = self.logs_dir / "trajectory.json"
            try:
                with open(traj_path, "w", encoding="utf-8") as fh:
                    json.dump(trajectory.to_json_dict(), fh, indent=2, ensure_ascii=False)
            except (OSError, AttributeError) as exc:
                self.logger.debug("Failed writing trajectory.json: %s", exc)
            fm = trajectory.final_metrics
            if fm:
                context.n_input_tokens = fm.total_prompt_tokens or 0
                context.n_output_tokens = fm.total_completion_tokens or 0
                context.n_cache_tokens = fm.total_cached_tokens or 0
                if fm.total_cost_usd is not None:
                    context.cost_usd = fm.total_cost_usd

    def _read_events(self) -> list[dict] | None:
        out = self.logs_dir / _OUTPUT_FILENAME
        if not out.is_file():
            self.logger.warning(
                "dsh output not found: %s — token/cost context will be empty "
                "(check the /logs/agent mount)",
                out,
            )
            return None
        events: list[dict] = []
        try:
            for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            self.logger.debug("Failed reading dsh output: %s", exc)
            return None
        return events

    def _build_trajectory(self, events: list[dict]) -> Trajectory | None:
        """Map dsh's --json NDJSON run events to an ATIF Trajectory.

        dsh emits committed content only: one Step per ``status`` step_end boundary
        (which carries the step's summed usage), tool calls from ``tool_call`` events,
        and a terminal ``final``. Totals are summed across step boundaries.
        """
        steps: list[Step] = []
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        session_id: str | None = None
        total_in = total_out = total_reason = total_cache = 0
        tool_errors = 0
        errors: list = []
        step_id = 1
        saw_final_error = False

        def flush(metrics: Metrics | None) -> None:
            nonlocal text_parts, reasoning_parts, tool_calls, step_id
            reasoning = "\n".join(p for p in reasoning_parts if p) or None
            steps.append(Step(
                step_id=step_id,
                source="agent",
                model_name=self.model_name,
                message="\n".join(p for p in text_parts if p),
                reasoning_content=reasoning if metrics else None,
                tool_calls=tool_calls or None,
                metrics=metrics,
                llm_call_count=1 if metrics else 0,
            ))
            text_parts, reasoning_parts, tool_calls = [], [], []
            step_id += 1

        for ev in events:
            etype = ev.get("type")
            if etype == "session":
                session_id = session_id or str(ev.get("sessionId") or "") or None
            elif etype == "text":
                text_parts.append(str(ev.get("text", "")))
            elif etype == "thinking":
                reasoning_parts.append(str(ev.get("text", "")))
            elif etype == "tool_call":
                args = ev.get("input")
                tool_calls.append(ToolCall(
                    tool_call_id=str(ev.get("callId") or ""),
                    function_name=str(ev.get("tool", "")),
                    arguments=args if isinstance(args, dict) else {},
                ))
            elif etype == "tool_result":
                if ev.get("status") == "error":
                    tool_errors += 1
            elif etype == "status" and ev.get("phase") == "step_end":
                usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
                t_in = int(usage.get("inputTokens") or 0)
                t_out = int(usage.get("outputTokens") or 0)
                t_reason = int(usage.get("reasoningTokens") or 0)
                t_cache = int(usage.get("cacheReadTokens") or 0)
                total_in += t_in
                total_out += t_out
                total_reason += t_reason
                total_cache += t_cache
                flush(Metrics(prompt_tokens=t_in, completion_tokens=t_out + t_reason))
            elif etype == "error":
                errors.append(ev)
            elif etype == "final":
                reason = ev.get("reason")
                if isinstance(reason, dict) and reason.get("kind") == "error":
                    saw_final_error = True
                    errors.append(reason)

        if text_parts or reasoning_parts or tool_calls:
            flush(None)

        if not steps and not errors:
            return None
        if not steps:
            steps.append(Step(
                step_id=1,
                source="agent",
                model_name=self.model_name,
                message=f"run produced no steps; {len(errors)} run error(s): "
                        + "; ".join(json.dumps(e)[:300] for e in errors[:3]),
                llm_call_count=0,
            ))

        extra: dict = {
            "steps": len(steps),
            "reasoning_tokens": total_reason,
            "tool_errors": tool_errors,
        }
        if errors:
            extra["dsh_errors"] = errors

        final_metrics = FinalMetrics(
            total_prompt_tokens=total_in,
            total_completion_tokens=total_out + total_reason,
            total_cached_tokens=total_cache,
            total_cost_usd=None,
            total_steps=len(steps),
            extra=extra,
        )

        notes = (
            "dsh emits one bounded NDJSON run-event stream; one Step = one step_end "
            "boundary carrying the step's summed usage. completion_tokens includes "
            "reasoning (raw split in extra.reasoning_tokens). No cost is reported; "
            "dsh does not emit prices."
        )
        if errors:
            notes += f" RUN REPORTED {len(errors)} error(s); see extra.dsh_errors."
            if saw_final_error:
                notes += " The terminal final event was an error (rollout cut short)."

        return Trajectory(
            session_id=session_id,
            agent=Agent(name="dsh", version=self._version or "unknown",
                        model_name=self.model_name),
            steps=steps,
            final_metrics=final_metrics,
            notes=notes,
        )
