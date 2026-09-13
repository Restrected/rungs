"""Shared helpers for the rungs test suite (unittest, no third-party packages).

Usage:
    from helpers import RungsTestCase
    class MyTest(RungsTestCase):
        def test_x(self):
            self.init_store()                     # temp project with .rungs/ and a roster
            code, out, err = self.run_cli("create", "--agent", "claude.fable.001", ...)
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rungs.cli import Context, main  # noqa: E402
from rungs.store import Store  # noqa: E402

DEFAULT_ROSTER = """project: testproj
agents:
  claude.fable.001:
    skill: xhigh
    role: director
  claude.opus.reviewer:
    skill: high
    role: reviewer
  openai.astra.001:
    skill: high
    role: executor
  claude.opus.001:
    skill: medium
    role: executor
  claude.sonnet.001:
    skill: low
    role: executor
  claude.haiku.001:
    skill: very-low
    role: executor
policy:
  review_required: true
  scope_overlap: refuse
  unregistered_agents: refuse
  stale_after_hours: 24
intake:
  default_skill: medium
  default_kind: fix
  default_priority: 15
  sources:
    helpdesk:
      epic: user-requests
      domain: ui
      tags: [user-request]
      ready: false
    trusted:
      epic: trusted
      ready: true
"""


class RungsTestCase(unittest.TestCase):
    """A temp project directory per test. `self.project` is its path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name).resolve()
        self._old_cwd = os.getcwd()
        os.chdir(self.project)
        os.environ.pop("RUNGS_AGENT", None)

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def init_store(self, roster_text: str = DEFAULT_ROSTER) -> Store:
        """Create .rungs/ with every folder and write rungs.yaml directly
        (without going through `rungs init`, so init's own tests stay honest)."""
        root = self.project / ".rungs"
        root.mkdir(exist_ok=True)
        store = Store(root)
        store.ensure_layout()
        (self.project / "rungs.yaml").write_text(roster_text, encoding="utf-8")
        return store

    def run_cli(self, *argv: str, cwd: Path | None = None):
        """Run the CLI in-process. Returns (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        ctx = Context(cwd=cwd or self.project, stdout=out, stderr=err)
        try:
            code = main(list(argv), ctx=ctx)
        except SystemExit as exc:  # argparse errors
            code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def read(self, relative: str) -> str:
        return (self.project / relative).read_text(encoding="utf-8")
