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

"""Render a runs/ tree into a self-contained HTML report.

    harness2-view --runs runs/demo -o runs/demo/report.html

Reads only what the method already wrote (summary.json, selection.json,
judged.json, evidence files); never starts a model or a benchmark. Works for any
benchmark's runs root and for the local demo's.
"""

from __future__ import annotations

import argparse

from ..viewer.load import load as load_tree
from ..viewer.render import write as write_report


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--runs", default="runs", help="runs root to read (default: %(default)s)")
    p.add_argument(
        "-o",
        "--output",
        default=None,
        help="output HTML path (default: <runs>/report.html)",
    )
    args = p.parse_args(argv)
    tree = load_tree(args.runs)
    out = write_report(tree, args.output or f"{args.runs.rstrip('/')}/report.html")
    judged = sum(1 for t in tree.tasks for m in t.judged.values() if m)
    print(
        f"{len(tree.tasks)} task record(s) across {len(tree.namespaces())} namespace(s), "
        f"{judged} judged selection(s) -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
