"""`rungs verify` (scope against a change set) and `rungs doctor`."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from helpers import RungsTestCase

DIRECTOR = "claude.fable.001"
REVIEWER = "claude.opus.reviewer"
OPUS = "claude.opus.001"
ASTRA = "openai.astra.001"
HAVE_GIT = shutil.which("git") is not None


class VerifyTest(RungsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = self.init_store()

    def make(self, scope="src/a.py", skill="medium", title="A ticket") -> str:
        code, out, err = self.run_cli(
            "create", "--agent", DIRECTOR, "--title", title, "--kind", "implement",
            "--skill", skill, "--scope", scope, "--goal", "g", "--acceptance", "- a")
        self.assertEqual(code, 0, err)
        return out.strip().splitlines()[-1]

    def submit(self, ticket_id, files, agent=OPUS):
        self.assertEqual(self.run_cli("claim", "--agent", agent, ticket_id)[0], 0)
        code, out, err = self.run_cli("submit", "--agent", agent, ticket_id,
                                      "--summary", "done", "--files", files)
        self.assertEqual(code, 0, err)

    def test_declared_files_inside_the_scope_pass(self):
        ticket_id = self.make(scope="src/,tests/test_a.py")
        self.submit(ticket_id, "src/deep/a.py,tests/test_a.py")
        code, out, err = self.run_cli("verify", ticket_id)
        self.assertEqual(code, 0, err)
        self.assertIn("every checked path is inside the scope", out)
        self.assertIn("declared at submit", out)

    def test_a_declared_file_outside_the_scope_exits_3(self):
        ticket_id = self.make(scope="src/a.py")
        self.submit(ticket_id, "src/a.py,src/b.py,docs/README.md")
        code, out, err = self.run_cli("verify", ticket_id)
        self.assertEqual(code, 3)
        self.assertIn("OUTSIDE SCOPE (2)", out)
        self.assertIn("src/b.py", out)
        self.assertIn("docs/README.md", out)
        self.assertIn("2 path(s) outside the scope", err)
        data = json.loads(self.run_cli("verify", ticket_id, "--json")[1])
        self.assertEqual(data["outside"], ["src/b.py", "docs/README.md"])
        self.assertFalse(data["ok"])

    def test_an_explicit_file_list_replaces_the_declared_one(self):
        ticket_id = self.make(scope="src/a.py")
        self.submit(ticket_id, "src/nowhere.py")
        code, out, err = self.run_cli("verify", ticket_id, "--files", "src/a.py")
        self.assertEqual(code, 0, err)
        self.assertIn("given with --files", out)
        self.assertNotIn("src/nowhere.py", out)

    def test_the_store_itself_is_never_judged(self):
        ticket_id = self.make(scope="src/a.py")
        self.submit(ticket_id, "src/a.py")
        code, out, err = self.run_cli("verify", ticket_id, "--files",
                                      ".rungs/open/x.md,rungs.yaml,src/a.py")
        self.assertEqual(code, 0, err)
        self.assertNotIn("rungs.yaml", out)

    def test_an_empty_scope_means_no_files(self):
        code, out, err = self.run_cli("create", "--agent", DIRECTOR, "--title", "design only",
                                      "--kind", "design", "--skill", "high", "--goal", "g",
                                      "--acceptance", "- a")
        ticket_id = out.strip().splitlines()[-1]
        self.assertEqual(self.run_cli("verify", ticket_id)[0], 0)
        code, out, err = self.run_cli("verify", ticket_id, "--files", "src/a.py")
        self.assertEqual(code, 3)
        self.assertIn("this ticket may change no files", out)

    @unittest.skipUnless(HAVE_GIT, "git is not installed")
    def test_tree_paths_are_labelled_and_declared_but_unchanged_files_are_warned_about(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True,
                       capture_output=True, text=True)
        (self.project / "src").mkdir()
        (self.project / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.project / "outside.py").write_text("y = 2\n", encoding="utf-8")
        ticket_id = self.make(scope="src/")
        self.submit(ticket_id, "src/a.py,src/never-touched.py")
        code, out, err = self.run_cli("verify", ticket_id, "--tree")
        self.assertEqual(code, 3)
        self.assertIn("may include another agent's concurrent edits", out)
        self.assertIn("outside.py", out)
        self.assertIn("declared at submit but not changed in the tree", out)
        self.assertIn("src/never-touched.py", out)


class DoctorTest(RungsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = self.init_store()

    def make(self, title="A ticket", skill="medium", scope=None) -> str:
        argv = ["create", "--agent", DIRECTOR, "--title", title, "--kind", "implement",
                "--skill", skill, "--goal", "g", "--acceptance", "- a"]
        if scope:
            argv += ["--scope", scope]
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, err)
        return out.strip().splitlines()[-1]

    def path_of(self, ticket_id):
        return self.store.find(ticket_id)[0]

    def test_a_clean_store_has_no_problems(self):
        self.make()
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 0, err)
        self.assertIn("no problems found", out)

    def test_folder_and_status_drift_is_found_and_repaired(self):
        ticket_id = self.make()
        path = self.path_of(ticket_id)
        moved = self.project / ".rungs" / "shelved" / path.name
        path.rename(moved)
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("[drift]", out)
        self.assertIn("the front matter says open but the file sits in shelved/", out)
        self.assertIn("can be repaired with `rungs doctor --fix`", err)

        code, out, err = self.run_cli("doctor", "--fix")
        self.assertEqual(code, 0, err)
        self.assertIn("status set to shelved to match the folder", out)
        self.assertIn("no problems found", out)
        self.assertEqual(json.loads(self.run_cli("show", ticket_id, "--json")[1])["status"], "shelved")

    def test_a_closed_ticket_in_the_wrong_month_is_refiled(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "d")
        self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "accept")
        path = self.path_of(ticket_id)
        wrong = self.project / ".rungs" / "closed" / "1999-01"
        wrong.mkdir(parents=True)
        path.rename(wrong / path.name)
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("[closed-month]", out)
        code, out, err = self.run_cli("doctor", "--fix")
        self.assertEqual(code, 0, err)
        self.assertIn("re-filed into closed/", out)
        self.assertFalse((wrong / path.name).exists())

    def test_a_missing_updated_time_is_filled_in(self):
        ticket_id = self.make()
        path = self.path_of(ticket_id)
        text = path.read_text(encoding="utf-8")
        line = [l for l in text.splitlines() if l.startswith("updated:")][0]
        path.write_text(text.replace(line, "updated: null"), encoding="utf-8")
        self.assertEqual(self.run_cli("doctor")[0], 3)
        code, out, err = self.run_cli("doctor", "--fix")
        self.assertEqual(code, 0, err)
        self.assertIn("updated set from created", out)

    def test_strays_and_unreadable_files_are_reported(self):
        self.make()
        (self.project / ".rungs" / "open" / "notes.txt").write_text("hello", encoding="utf-8")
        (self.project / ".rungs" / "open" / ".rungs-tmp-abc").write_text("half", encoding="utf-8")
        (self.project / ".rungs" / "open" / "rg-bbbbb.md").write_text("not a ticket\n", encoding="utf-8")
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("[stray]", out)
        self.assertIn("notes.txt", out)
        self.assertIn(".rungs-tmp-abc", out)
        self.assertIn("[unreadable]", out)
        self.assertIn("rg-bbbbb.md", out)

    def test_dangling_self_and_circular_dependencies_are_found(self):
        first = self.make(title="first")
        second = self.make(title="second")
        self.run_cli("depend", "--agent", DIRECTOR, second, "--on", first)
        path = self.path_of(first)
        path.write_text(path.read_text(encoding="utf-8").replace(
            "depends_on: []", f"depends_on: [{second}, {first}, rg-00000]"), encoding="utf-8")
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("[dangling-dependency]", out)
        self.assertIn("rg-00000", out)
        self.assertIn("[self-dependency]", out)
        self.assertIn("[cycle]", out)
        self.assertIn("[invalid]", out)

    def test_duplicate_ids_and_origins_are_found(self):
        first = self.make(title="first")
        second = self.make(title="second")
        shutil.copy(self.path_of(first), self.project / ".rungs" / "shelved" / f"{first}.md")
        for ticket_id in (first, second):
            path = self.store.find(ticket_id)[0]
            path.write_text(path.read_text(encoding="utf-8").replace(
                "origin: null", "origin: helpdesk:42"), encoding="utf-8")
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("[duplicate-id]", out)
        self.assertIn("[duplicate-origin]", out)
        self.assertIn("helpdesk:42", out)

    def test_a_bad_field_value_is_reported_as_invalid(self):
        ticket_id = self.make()
        path = self.path_of(ticket_id)
        path.write_text(path.read_text(encoding="utf-8").replace("skill: medium", "skill: enormous"),
                        encoding="utf-8")
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("[invalid]", out)
        self.assertIn("enormous", out)

    def test_two_live_tickets_holding_the_same_scope_are_reported(self):
        first = self.make(title="one", scope="src/rungs/")
        second = self.make(title="two", skill="high", scope="src/rungs/store.py")
        self.run_cli("claim", "--agent", OPUS, first)
        self.assertEqual(self.run_cli("claim", "--agent", ASTRA, second, "--allow-overlap")[0], 0)
        code, out, err = self.run_cli("doctor")
        self.assertEqual(code, 3)
        self.assertIn("[live-overlap]", out)
        self.assertIn(first, out)
        self.assertIn(second, out)
        data = json.loads(self.run_cli("doctor", "--json")[1])
        self.assertFalse(data["ok"])
        self.assertEqual([p["kind"] for p in data["problems"]], ["live-overlap"])

    def test_fixing_is_written_to_the_event_log(self):
        ticket_id = self.make()
        path = self.path_of(ticket_id)
        path.rename(self.project / ".rungs" / "shelved" / path.name)
        self.run_cli("doctor", "--fix")
        events = json.loads(self.run_cli("log", "--json")[1])
        self.assertTrue(any(e["action"] == "doctor" for e in events))


if __name__ == "__main__":
    unittest.main()
