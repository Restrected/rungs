"""Tests for rungs.scope: the matching contract, overlap, git change sets,
and verify. See DESIGN.md section 6."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (inserts src/ onto sys.path)

from rungs import scope as S
from rungs import ticket as T


def mk(id, scope=None, status="open", **kw):
    return T.Ticket(
        id=id, title=f"title {id}", status=status, kind="implement", skill="medium",
        created="2026-09-13T00:00:00", updated="2026-09-13T00:00:00",
        scope=scope or [], **kw
    )


class NormaliseTests(unittest.TestCase):
    def test_strips_leading_dot_slash(self):
        self.assertEqual(S.normalise("./src/a.py"), "src/a.py")

    def test_strips_multiple_leading_dot_slash(self):
        self.assertEqual(S.normalise("././src/a.py"), "src/a.py")

    def test_backslashes_become_forward_slashes(self):
        self.assertEqual(S.normalise("src\\a.py"), "src/a.py")

    def test_collapses_double_slashes(self):
        self.assertEqual(S.normalise("src//a.py"), "src/a.py")

    def test_rejects_absolute_path(self):
        with self.assertRaises(S.ScopeError):
            S.normalise("/etc/passwd")

    def test_rejects_dotdot_component(self):
        with self.assertRaises(S.ScopeError):
            S.normalise("a/../b")

    def test_rejects_leading_dotdot(self):
        with self.assertRaises(S.ScopeError):
            S.normalise("../outside")

    def test_rejects_empty(self):
        with self.assertRaises(S.ScopeError):
            S.normalise("")

    def test_rejects_dot(self):
        with self.assertRaises(S.ScopeError):
            S.normalise(".")

    def test_plain_relative_path_unchanged(self):
        self.assertEqual(S.normalise("src/a.py"), "src/a.py")


class ClassifyTests(unittest.TestCase):
    def test_directory(self):
        self.assertEqual(S.classify("src/"), "dir")

    def test_literal(self):
        self.assertEqual(S.classify("src/a.py"), "literal")

    def test_glob_star(self):
        self.assertEqual(S.classify("src/*.py"), "glob")

    def test_glob_question(self):
        self.assertEqual(S.classify("src/?.py"), "glob")

    def test_glob_charclass(self):
        self.assertEqual(S.classify("src/[ab].py"), "glob")


class CoversTests(unittest.TestCase):
    def test_dir_covers_nested_file(self):
        self.assertTrue(S.covers("src/", "src/sub/deep/file.py"))

    def test_dir_does_not_cover_sibling(self):
        self.assertFalse(S.covers("src/", "srcx/file.py"))

    def test_literal_matches_only_exact_path(self):
        self.assertTrue(S.covers("src/a.py", "src/a.py"))
        self.assertFalse(S.covers("src/a.py", "src/b.py"))

    def test_star_does_not_cross_slash(self):
        self.assertTrue(S.covers("src/*.py", "src/a.py"))
        self.assertFalse(S.covers("src/*.py", "src/sub/a.py"))

    def test_double_star_crosses_directories(self):
        self.assertTrue(S.covers("src/**/*.py", "src/a/b/c.py"))
        self.assertTrue(S.covers("src/**/*.py", "src/c.py"))

    def test_question_matches_one_char(self):
        self.assertTrue(S.covers("src/?.py", "src/a.py"))
        self.assertFalse(S.covers("src/?.py", "src/ab.py"))

    def test_character_class(self):
        self.assertTrue(S.covers("src/[ab].py", "src/a.py"))
        self.assertTrue(S.covers("src/[ab].py", "src/b.py"))
        self.assertFalse(S.covers("src/[ab].py", "src/c.py"))

    def test_scope_covers_any_entry(self):
        self.assertTrue(S.scope_covers(["docs/", "src/a.py"], "src/a.py"))
        self.assertFalse(S.scope_covers(["docs/", "src/a.py"], "src/b.py"))


class OverlapTableTests(unittest.TestCase):
    """The exact table from DESIGN.md section 6, checked in both argument
    orders since overlaps() is documented as symmetric."""

    CASES = [
        ("src/a.py", "src/b.py", False, "different literals"),
        ("src/a.py", "src/", True, "prefix"),
        ("src/", "src/sub/", True, "prefix"),
        ("src/*.py", "src/a.py", True, "glob covers the literal"),
        ("src/*.py", "src/sub/a.py", False, "* does not cross /"),
        ("src/*.py", "src/test_*", True, "globs: comparable literal prefixes"),
        ("src/*.py", "docs/*.md", False, "literal prefixes differ"),
        ("src/**", "src/sub/x.py", True, "** crosses directories"),
        ("**/*.md", "docs/", True, "empty literal prefix is comparable with everything"),
    ]

    def test_table_forward(self):
        for a, b, expected, why in self.CASES:
            with self.subTest(a=a, b=b, why=why):
                self.assertEqual(S.overlaps(a, b), expected)

    def test_table_reversed(self):
        for a, b, expected, why in self.CASES:
            with self.subTest(a=a, b=b, why=why):
                self.assertEqual(S.overlaps(b, a), expected)

    def test_identical_literals_overlap(self):
        self.assertTrue(S.overlaps("src/a.py", "src/a.py"))


class ScopeOverlapAndTicketsTests(unittest.TestCase):
    def test_scope_overlap_returns_matching_pairs(self):
        pairs = S.scope_overlap(["src/a.py", "docs/"], ["src/a.py", "other/"])
        self.assertIn(("src/a.py", "src/a.py"), pairs)

    def test_scope_overlap_empty_when_disjoint(self):
        self.assertEqual(S.scope_overlap(["src/a.py"], ["docs/b.md"]), [])

    def test_overlapping_tickets_only_live_statuses(self):
        ticket = mk("rg-1", scope=["src/a.py"])
        in_progress = mk("rg-2", scope=["src/a.py"], status="in_progress")
        review = mk("rg-3", scope=["src/a.py"], status="review")
        closed = mk("rg-4", scope=["src/a.py"], status="closed")
        open_one = mk("rg-5", scope=["src/a.py"], status="open")
        result = S.overlapping_tickets(ticket, [in_progress, review, closed, open_one])
        ids = {t.id for t, _ in result}
        self.assertEqual(ids, {"rg-2", "rg-3"})

    def test_overlapping_tickets_excludes_self(self):
        ticket = mk("rg-1", scope=["src/a.py"], status="in_progress")
        result = S.overlapping_tickets(ticket, [ticket])
        self.assertEqual(result, [])

    def test_overlapping_tickets_no_overlap(self):
        ticket = mk("rg-1", scope=["src/a.py"])
        other = mk("rg-2", scope=["docs/b.md"], status="in_progress")
        self.assertEqual(S.overlapping_tickets(ticket, [other]), [])

    def test_overlapping_tickets_custom_statuses(self):
        ticket = mk("rg-1", scope=["src/a.py"])
        blocked = mk("rg-2", scope=["src/a.py"], status="blocked")
        result = S.overlapping_tickets(ticket, [blocked], statuses={"blocked"})
        self.assertEqual(len(result), 1)


class VerifyTests(unittest.TestCase):
    def test_declared_outside_scope_is_flagged(self):
        ticket = mk("rg-1", scope=["src/rungs/store.py"])
        result = S.verify(ticket, declared=["src/rungs/store.py", "src/rungs/other.py"])
        self.assertFalse(result.ok)
        self.assertEqual(result.outside, ["src/rungs/other.py"])

    def test_declared_inside_scope_is_ok(self):
        ticket = mk("rg-1", scope=["src/rungs/store.py"])
        result = S.verify(ticket, declared=["src/rungs/store.py"])
        self.assertTrue(result.ok)
        self.assertEqual(result.outside, [])

    def test_ignored_paths_are_skipped(self):
        ticket = mk("rg-1", scope=["src/rungs/store.py"])
        result = S.verify(
            ticket, declared=["src/rungs/store.py"],
            tree_files=[".rungs/foo.md", "rungs.yaml", ".rungs.yaml", "src/rungs/store.py"],
        )
        self.assertTrue(result.ok)
        self.assertNotIn(".rungs/foo.md", result.checked)
        self.assertNotIn("rungs.yaml", result.checked)

    def test_declared_not_changed_only_when_tree_given(self):
        ticket = mk("rg-1", scope=["src/rungs/store.py", "src/rungs/other.py"])
        # No tree_files given at all: declared_not_changed must stay empty.
        result_no_tree = S.verify(ticket, declared=["src/rungs/store.py"])
        self.assertEqual(result_no_tree.declared_not_changed, [])
        # tree_files given but doesn't include the declared file.
        result_with_tree = S.verify(
            ticket, declared=["src/rungs/store.py"], tree_files=["src/rungs/other.py"]
        )
        self.assertEqual(result_with_tree.declared_not_changed, ["src/rungs/store.py"])

    def test_explicit_file_list(self):
        ticket = mk("rg-1", scope=["src/rungs/store.py"])
        result = S.verify(ticket, explicit=["docs/readme.md"])
        self.assertFalse(result.ok)
        self.assertEqual(result.outside, ["docs/readme.md"])

    def test_no_scope_means_no_files_allowed(self):
        ticket = mk("rg-1", scope=[])
        result = S.verify(ticket, declared=["anything.py"])
        self.assertFalse(result.ok)
        self.assertEqual(result.outside, ["anything.py"])

    def test_duplicate_paths_across_sources_counted_once(self):
        ticket = mk("rg-1", scope=["src/a.py"])
        result = S.verify(ticket, declared=["src/a.py"], tree_files=["src/a.py"])
        self.assertEqual(result.checked, ["src/a.py"])


class GitChangedTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not available on this machine")
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self._run("init", "-q")
        self._run("config", "user.email", "test@example.com")
        self._run("config", "user.name", "Test")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True
        )
        return result.stdout

    def test_tree_reports_modified_and_untracked(self):
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        self._run("add", "a.txt")
        self._run("commit", "-q", "-m", "init")
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        (self.repo / "b.txt").write_text("new\n", encoding="utf-8")
        changed = S.git_changed(self.repo, tree=True)
        self.assertEqual(changed, ["a.txt", "b.txt"])

    def test_staged_reports_index_changes(self):
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        self._run("add", "a.txt")
        self._run("commit", "-q", "-m", "init")
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        self._run("add", "a.txt")
        changed = S.git_changed(self.repo, staged=True)
        self.assertEqual(changed, ["a.txt"])

    def test_no_flags_returns_empty(self):
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        self._run("add", "a.txt")
        self._run("commit", "-q", "-m", "init")
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        self.assertEqual(S.git_changed(self.repo), [])

    def test_since_reports_commits_after_ref(self):
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        self._run("add", "a.txt")
        self._run("commit", "-q", "-m", "init")
        base = self._run("rev-parse", "HEAD").strip()
        (self.repo / "b.txt").write_text("new\n", encoding="utf-8")
        self._run("add", "b.txt")
        self._run("commit", "-q", "-m", "second")
        changed = S.git_changed(self.repo, since=base)
        self.assertEqual(changed, ["b.txt"])


if __name__ == "__main__":
    unittest.main()
