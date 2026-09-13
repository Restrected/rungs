"""`rungs init` and `rungs deploy`.

Everything here runs against temporary directories only: the symlink tests
pass their own `--bin` under the test's project directory, so nothing is ever
written to the real `~/.local/bin`.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import RungsTestCase  # noqa: E402

from rungs import deploy  # noqa: E402
from rungs.store import Store  # noqa: E402

CHECKOUT = Path(__file__).resolve().parents[1]


class InitTests(RungsTestCase):
    def test_init_creates_the_layout_the_roster_and_the_docs(self):
        code, out, err = self.run_cli(
            "init", "--project", "demo",
            "--agent", "claude.fable.001:xhigh:director",
            "--agent", "claude.opus.001:medium",
        )
        self.assertEqual(code, 0, err)
        root = self.project / ".rungs"
        for folder in ("triage", "open", "in_progress", "review", "blocked", "shelved",
                       "closed", "inbox", "inbox/done", "plans", "agents"):
            self.assertTrue((root / folder).is_dir(), folder)
        self.assertTrue((self.project / "rungs.yaml").is_file())
        self.assertTrue((root / "AGENTS.md").is_file())
        self.assertTrue((root / "agents" / "claude.fable.001.md").is_file())
        self.assertTrue((root / "agents" / "claude.opus.001.md").is_file())
        self.assertIn("created", out)
        roster = Store(root).roster()
        self.assertEqual(roster.project, "demo")
        self.assertEqual(roster.agents["claude.fable.001"].role, "director")
        self.assertEqual(roster.agents["claude.opus.001"].skill, "medium")

    def test_init_is_idempotent(self):
        self.run_cli("init", "--project", "demo", "--agent", "claude.opus.001:medium")
        first_yaml = self.read("rungs.yaml")
        first_guide = self.read(".rungs/AGENTS.md")
        code, out, err = self.run_cli("init", "--project", "demo")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("rungs.yaml"), first_yaml)
        self.assertEqual(self.read(".rungs/AGENTS.md"), first_guide)
        self.assertIn("is unchanged", out)
        self.assertIn("already in place", out)

    def test_init_merges_a_new_agent_into_an_existing_roster(self):
        self.run_cli("init", "--project", "demo", "--agent", "claude.opus.001:medium")
        code, out, err = self.run_cli(
            "init", "--agent", "claude.haiku.001:very-low", "--agent", "claude.opus.001:medium")
        self.assertEqual(code, 0, err)
        roster = Store(self.project / ".rungs").roster()
        self.assertEqual(sorted(roster.agents), ["claude.haiku.001", "claude.opus.001"])
        self.assertIn("updated", out)
        # Rewriting the file loses comments; the user is told so.
        self.assertIn("comments", err)

    def test_init_updates_an_agent_in_place_rather_than_duplicating_it(self):
        self.run_cli("init", "--agent", "claude.opus.001:medium")
        self.run_cli("init", "--agent", "claude.opus.001:high:reviewer")
        roster = Store(self.project / ".rungs").roster()
        self.assertEqual(len(roster.agents), 1)
        self.assertEqual(roster.agents["claude.opus.001"].skill, "high")
        self.assertEqual(roster.agents["claude.opus.001"].role, "reviewer")

    def test_init_with_no_agents_says_they_must_be_added(self):
        code, out, err = self.run_cli("init", "--project", "demo")
        self.assertEqual(code, 0, err)
        self.assertIn("no agents are registered yet", out)
        self.assertIn("# claude.fable.001:", self.read("rungs.yaml"))

    def test_init_rejects_a_bad_agent_spec(self):
        code, _out, err = self.run_cli("init", "--agent", "not/an/id:medium")
        self.assertEqual(code, 1)
        self.assertIn("is not valid", err)

    def test_init_json(self):
        _code, out, _err = self.run_cli("init", "--agent", "claude.opus.001:medium", "--json")
        result = json.loads(out)
        self.assertTrue(result["created_config"])
        self.assertEqual(result["agents"], ["claude.opus.001"])
        self.assertEqual(len(result["docs"]), 2)


class DeployTests(RungsTestCase):
    def setUp(self):
        super().setUp()
        self.target = self.project / "app"
        self.target.mkdir()
        self.bin = self.project / "bin"
        self.bin.mkdir()

    def deploy(self, *extra):
        return self.run_cli(
            "deploy", "--into", str(self.target), "--bin", str(self.bin), "--no-doctor", *extra)

    def test_deploy_links_the_command_and_initialises_the_project(self):
        code, out, err = self.deploy("--project", "app", "--agent", "claude.opus.001:medium")
        self.assertEqual(code, 0, err)
        link = self.bin / "rungs"
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(os.readlink(str(link))), CHECKOUT / "bin" / "rungs")
        self.assertTrue((self.target / ".rungs" / "open").is_dir())
        self.assertTrue((self.target / "rungs.yaml").is_file())
        self.assertTrue((self.target / ".rungs" / "AGENTS.md").is_file())
        self.assertTrue((self.target / ".rungs" / "agents" / "claude.opus.001.md").is_file())
        # Step 7: the two lines for the project's own instructions.
        self.assertIn("add these two lines", out)
        self.assertIn("Read .rungs/AGENTS.md", out)
        self.assertIn("rungs next --agent <your id> --claim", out)
        # The bin directory is not on PATH in the test environment: say so.
        self.assertIn("not on your PATH", err)

    def test_deploy_is_idempotent(self):
        self.deploy("--agent", "claude.opus.001:medium")
        first = self.read("app/rungs.yaml")
        code, _out, err = self.deploy("--agent", "claude.opus.001:medium")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("app/rungs.yaml"), first)

    def test_deploy_replaces_a_symlink_that_points_elsewhere(self):
        link = self.bin / "rungs"
        link.symlink_to(self.project / "somewhere-else")
        code, out, err = self.deploy()
        self.assertEqual(code, 0, err)
        self.assertEqual(Path(os.readlink(str(link))), CHECKOUT / "bin" / "rungs")
        self.assertIn("replaced the rungs symlink", out)

    def test_deploy_refuses_to_overwrite_a_regular_file(self):
        link = self.bin / "rungs"
        link.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        code, _out, err = self.deploy()
        self.assertEqual(code, 1)
        self.assertIn("is not a symlink", err)
        self.assertEqual(link.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")
        # Nothing else ran: step 1 failed, so the store was not created.
        self.assertFalse((self.target / ".rungs").exists())

    def test_vendor_mode_copies_the_tool_into_the_project(self):
        code, out, err = self.run_cli(
            "deploy", "--into", str(self.target), "--vendor", "--no-doctor",
            "--agent", "claude.opus.001:medium")
        self.assertEqual(code, 0, err)
        vendored = self.target / "tools" / "rungs"
        self.assertTrue((vendored / "bin" / "rungs").is_file())
        self.assertTrue((vendored / "src" / "rungs" / "cli.py").is_file())
        self.assertTrue((vendored / "src" / "rungs" / "intake.py").is_file())
        wrapper = vendored / "rungs"
        self.assertTrue(wrapper.is_file())
        self.assertTrue(os.access(str(wrapper), os.X_OK))
        self.assertIn('exec python3 "$(dirname "$0")/bin/rungs" "$@"', wrapper.read_text())
        # No compiled leftovers travel with it.
        self.assertEqual(list(vendored.rglob("__pycache__")), [])
        self.assertIn("vendored the tool into", out)
        # The project is initialised the same way as in link mode.
        self.assertTrue((self.target / ".rungs" / "AGENTS.md").is_file())

    def test_vendor_mode_is_repeatable(self):
        args = ("deploy", "--into", str(self.target), "--vendor", "--no-doctor")
        code, _out, err = self.run_cli(*args)
        self.assertEqual(code, 0, err)
        # A second run over an untouched vendored tree replaces it without complaint.
        code, _out, err = self.run_cli(*args)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.target / "tools" / "rungs" / "src" / "rungs" / "cli.py").is_file())

    def test_vendor_refuses_to_destroy_files_it_did_not_write(self):
        args = ("deploy", "--into", str(self.target), "--vendor", "--no-doctor")
        self.run_cli(*args)
        vendored = self.target / "tools" / "rungs"
        theirs = vendored / "src" / "rungs" / "local_patch.py"
        theirs.write_text("# somebody's own work\n", encoding="utf-8")
        code, _out, err = self.run_cli(*args)
        self.assertEqual(code, 1)
        self.assertIn("did not put there", err)
        self.assertIn("local_patch.py", err)
        self.assertIn("--force", err)
        # Nothing was touched: the file is still there and so is the tool.
        self.assertTrue(theirs.is_file())
        self.assertTrue((vendored / "src" / "rungs" / "cli.py").is_file())

    def test_vendor_force_replaces_the_tree(self):
        args = ("deploy", "--into", str(self.target), "--vendor", "--no-doctor")
        self.run_cli(*args)
        stale = self.target / "tools" / "rungs" / "src" / "rungs" / "leftover.py"
        stale.write_text("# from an older version\n", encoding="utf-8")
        code, _out, err = self.run_cli(*args, "--force")
        self.assertEqual(code, 0, err)
        self.assertFalse(stale.exists())

    def test_compiled_leftovers_do_not_count_as_foreign(self):
        args = ("deploy", "--into", str(self.target), "--vendor", "--no-doctor")
        self.run_cli(*args)
        cache = self.target / "tools" / "rungs" / "src" / "rungs" / "__pycache__"
        cache.mkdir()
        (cache / "cli.cpython-39.pyc").write_bytes(b"\x00")
        code, _out, err = self.run_cli(*args)
        self.assertEqual(code, 0, err)

    def test_force_never_overrides_the_regular_file_refusal(self):
        link = self.bin / "rungs"
        link.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        code, _out, err = self.deploy("--force")
        self.assertEqual(code, 1)
        self.assertIn("is not a symlink", err)
        self.assertEqual(link.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")

    def test_deploy_imports_an_arbite_store_when_asked(self):
        arbite = self.target / ".arbite" / "open"
        arbite.mkdir(parents=True)
        (arbite / "tic-1234.md").write_text(
            "---\nid: tic-1234\ntitle: An old ticket\nstatus: open\ntype: bug\ntier: low\n"
            "domain: ui\nepic: legacy\npriority: 3\ntags:\n- old\nassignee: null\n"
            "depends_on: []\nblocked_by: null\ncreated: '2026-09-01T10:00:00'\n"
            "updated: '2026-09-01T10:00:00'\nclosed: null\n---\n\n## Description\nOld work.\n\n"
            "## Notes\n",
            encoding="utf-8",
        )
        code, out, err = self.deploy("--agent", "claude.opus.001:medium", "--import-arbite")
        self.assertEqual(code, 0, err)
        self.assertIn("imported 1 ticket(s)", out)
        tickets = Store(self.target / ".rungs").by_id()
        self.assertEqual(tickets["tic-1234"].kind, "fix")
        self.assertEqual(tickets["tic-1234"].origin, "arbite:tic-1234")

    def test_deploy_reports_a_missing_arbite_store_and_exits_non_zero(self):
        code, _out, err = self.deploy("--import-arbite", str(self.project / "nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("no arbite store at", err)
        self.assertIn("did not finish cleanly", err)

    def test_doctor_step_is_skipped_cleanly_when_the_command_is_absent(self):
        class Stub:
            subparsers = {}

            def warn(self, text):
                pass

        self.assertIsNone(deploy._run_doctor(Stub()))

    def test_the_checkout_root_is_this_repository(self):
        self.assertEqual(deploy.checkout_root(), CHECKOUT)
        self.assertTrue((deploy.checkout_root() / "bin" / "rungs").is_file())


if __name__ == "__main__":
    unittest.main()
