"""DshAgent subclass that installs the on-demand harness components from WB_HARNESS_WS
into the trial's isolated DSH_HOME; an empty workspace reproduces stock dsh.

The always-on text itself is NOT staged here: under the home_dir_jinja install mode
the bench renders the composed prompt template into the instruction dsh receives, so
this class only delivers what the prompt advertises — skills into $DSH_HOME/skills
(the dsh-skill-filesystem user root) and helper scripts into ~/harness_scripts.
"""

from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

from harbor.environments.base import BaseEnvironment

from workbuddy_bench.agents.dsh_agent import DshAgent

_SKILL_SUBDIR = ".dsh-trial/skills"
_SCRIPT_SUBDIR = "harness_scripts"


class DshHarnessAgent(DshAgent):
    """dsh + a mounted harness workspace."""

    def __init__(self, logs_dir: Path, *args, **kwargs):
        ws = kwargs.pop("WB_HARNESS_WS", None)
        self._harness_ws: Path | None = Path(ws) if ws else None
        super().__init__(logs_dir, *args, **kwargs)

    @staticmethod
    def name() -> str:
        return "dsh-harness"

    async def _run_dsh(
        self, environment: BaseEnvironment, *, base_url: str, api_key: str,
        escaped_instruction: str,
    ) -> None:
        if self._harness_ws is not None:
            await self._materialize_harness(environment)
        await super()._run_dsh(
            environment, base_url=base_url, api_key=api_key,
            escaped_instruction=escaped_instruction,
        )

    async def _materialize_harness(self, environment: BaseEnvironment) -> None:
        """Install skills and scripts into the trial's DSH_HOME, as one base64 tar.

        DSH_HOME is created by dsh_agent's run command, but the staged dirs must exist
        before it, so both are mkdir'd here relative to $HOME — the same home the run
        command exports.
        """
        ws = self._harness_ws
        if ws is None or not ws.is_dir():
            return

        payload = io.BytesIO()
        n = 0
        with tarfile.open(fileobj=payload, mode="w:gz") as tar:
            for src_name, dest in (("skills", _SKILL_SUBDIR), ("scripts", _SCRIPT_SUBDIR)):
                src = ws / src_name
                if not src.is_dir():
                    continue
                for p in sorted(src.rglob("*")):
                    if p.is_file():
                        tar.add(p, arcname=f"{dest}/{p.relative_to(src)}")
                        n += 1
        if n == 0:
            return

        blob = base64.b64encode(payload.getvalue()).decode()
        home_fix = 'export HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"; '
        cmd = (
            home_fix
            + f'mkdir -p "$HOME/{_SKILL_SUBDIR}" "$HOME/{_SCRIPT_SUBDIR}" && '
            + f'echo {blob} | base64 -d | tar -xzf - -C "$HOME" && '
            + f'chmod -R a+rX "$HOME/{_SKILL_SUBDIR}" "$HOME/{_SCRIPT_SUBDIR}"'
        )
        await self.exec_as_agent(environment, command=cmd)
        log = getattr(self, "logger", None)
        if log is not None:
            log.info("materialized %d harness file(s) from %s", n, ws)
