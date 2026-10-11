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

"""The substrate registry: the one place a CLI token becomes a substrate object.

Adding a substrate means adding one entry here plus the per-benchmark wiring it
declares support for (install mode, launcher body, base harness). Everything else --
CLI ``--substrate`` choices, adapter construction, help text -- derives from this
module, so the token set is never enumerated twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .base import Launcher, Substrate
from .codex import CodexSubstrate
from .dsh import DshSubstrate
from .opencode import OpencodeSubstrate


@dataclass(frozen=True)
class SubstrateEntry:
    """One registered substrate: its CLI token, display label, and factory."""

    key: str
    display: str
    build: Callable[[Launcher | None], Substrate]


def _build_opencode(launcher: Launcher | None) -> OpencodeSubstrate:
    return OpencodeSubstrate(launcher=launcher)


def _build_codex(launcher: Launcher | None) -> CodexSubstrate:
    return CodexSubstrate(launcher=launcher)


def _build_dsh(launcher: Launcher | None) -> DshSubstrate:
    return DshSubstrate(launcher=launcher)


ENTRIES: dict[str, SubstrateEntry] = {
    "oc": SubstrateEntry("oc", "opencode", _build_opencode),
    "cx": SubstrateEntry("cx", "codex", _build_codex),
    "dsh": SubstrateEntry("dsh", "dsh headless", _build_dsh),
}


def names() -> tuple[str, ...]:
    return tuple(ENTRIES)


def help_text() -> str:
    return " | ".join(f"{e.key} ({e.display})" for e in ENTRIES.values())


def build(key: str, launcher: Launcher | None = None) -> Substrate:
    try:
        entry = ENTRIES[key]
    except KeyError:
        raise ValueError(
            f"unknown substrate {key!r}; expected one of {names()}"
        ) from None
    return entry.build(launcher)


__all__ = ["SubstrateEntry", "ENTRIES", "build", "help_text", "names"]
