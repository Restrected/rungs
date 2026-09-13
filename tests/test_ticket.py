"""Tests for rungs.ticket: vocabularies, body sections, atomic io, validate,
find_cycles. See DESIGN.md section 3."""

import unittest
from pathlib import Path

import helpers  # noqa: F401  (inserts src/ onto sys.path)

from rungs import ticket as T


def make_ticket(**overrides):
    base = dict(
        id="rg-00001", title="A title", status="open", kind="implement", skill="medium",
        created="2026-09-13T00:00:00", updated="2026-09-13T00:00:00",
    )
    base.update(overrides)
    return T.Ticket(**base)


class NormaliseSkillTests(unittest.TestCase):
    def test_canonical_names(self):
        for name in T.SKILLS:
            self.assertEqual(T.normalise_skill(name), name)

    def test_aliases(self):
        aliases = {
            "vlow": "very-low", "xlow": "very-low", "very_low": "very-low", "1": "very-low",
            "2": "low", "med": "medium", "mid": "medium", "3": "medium",
            "4": "high", "very-high": "xhigh", "vhigh": "xhigh", "extra-high": "xhigh", "5": "xhigh",
        }
        for alias, expected in aliases.items():
            with self.subTest(alias=alias):
                self.assertEqual(T.normalise_skill(alias), expected)

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(T.normalise_skill(" MEDIUM \n"), "medium")

    def test_unknown_skill_raises(self):
        with self.assertRaises(T.TicketError):
            T.normalise_skill("nonsense")

    def test_skill_rank(self):
        self.assertEqual([T.skill_rank(s) for s in T.SKILLS], [1, 2, 3, 4, 5])
        self.assertEqual(T.skill_rank("vlow"), 1)


class TextHelperTests(unittest.TestCase):
    def test_clean_text_removes_control_chars(self):
        self.assertEqual(T.clean_text("a\x00b\x1fc"), "abc")

    def test_clean_text_normalises_crlf(self):
        self.assertEqual(T.clean_text("a\r\nb\rc\n"), "a\nb\nc")

    def test_clean_text_breaks_dash_runs(self):
        self.assertEqual(T.clean_text("---"), "- - -")
        self.assertEqual(T.clean_text("----"), "- - - -")
        self.assertEqual(T.clean_text("a---b"), "a- - -b")
        # no run of 3+ dashes remains
        for word in ("---", "----", "-----", "a---b"):
            self.assertNotIn("---", T.clean_text(word))

    def test_clean_text_strips_ends(self):
        self.assertEqual(T.clean_text("  hello  \n"), "hello")

    def test_one_line_flattens_newlines_and_tabs(self):
        self.assertEqual(T.one_line("a\nb\tc"), "a b c")

    def test_one_line_collapses_whitespace(self):
        self.assertEqual(T.one_line("a    b"), "a b")

    def test_one_line_no_limit(self):
        text = "x" * 50
        self.assertEqual(T.one_line(text), text)

    def test_one_line_truncates_with_ellipsis(self):
        result = T.one_line("a" * 10, limit=5)
        self.assertEqual(len(result), 5)
        self.assertTrue(result.endswith("…"))
        self.assertEqual(result, "aaaa…")

    def test_one_line_under_limit_untouched(self):
        self.assertEqual(T.one_line("abc", limit=10), "abc")


class SectionsTests(unittest.TestCase):
    def test_split_sections_basic(self):
        body = "## Goal\nDo the thing.\n\n## Notes\n- note 1\n"
        sections = T.split_sections(body)
        self.assertEqual(sections["Goal"], "Do the thing.")
        self.assertEqual(sections["Notes"], "- note 1")

    def test_split_sections_preamble(self):
        body = "Some preamble text.\n\n## Goal\nDo it.\n"
        sections = T.split_sections(body)
        self.assertEqual(sections[""], "Some preamble text.")
        self.assertEqual(sections["Goal"], "Do it.")

    def test_fenced_code_heading_is_not_a_heading(self):
        body = (
            "## Goal\n"
            "line1\n\n"
            "```python\n"
            "## not a heading\n"
            "x = 1\n"
            "```\n\n"
            "## Notes\n"
            "- note1\n"
        )
        sections = T.split_sections(body)
        self.assertNotIn("not a heading", sections)
        self.assertIn("```python\n## not a heading\nx = 1\n```", sections["Goal"])
        self.assertEqual(sections["Notes"], "- note1")

    def test_fenced_code_with_tilde_fence(self):
        body = "## Goal\n~~~\n## still not a heading\n~~~\n"
        sections = T.split_sections(body)
        self.assertIn("still not a heading", sections["Goal"])
        self.assertNotIn("still not a heading", sections)

    def test_render_body_canonical_order(self):
        rendered = T.render_body({"Notes": "- n1", "Goal": "g1"})
        goal_idx = rendered.index("## Goal")
        notes_idx = rendered.index("## Notes")
        self.assertLess(goal_idx, notes_idx)
        for name in T.SECTIONS:
            self.assertIn(f"## {name}", rendered)

    def test_render_body_extra_sections_preserved(self):
        rendered = T.render_body({"Goal": "g1", "Extra Heading": "e1"})
        self.assertIn("## Extra Heading", rendered)
        self.assertIn("e1", rendered)
        # extra sections come after the canonical ones
        self.assertGreater(rendered.index("## Extra Heading"), rendered.index("## Notes"))

    def test_render_body_preamble(self):
        rendered = T.render_body({"": "intro text", "Goal": "g1"})
        self.assertTrue(rendered.startswith("intro text\n\n## Goal"))

    def test_split_then_render_round_trip(self):
        # render_body always emits every canonical section (even empty ones)
        # so the template stays visible; a full split(render(split(x))) is
        # the meaningful round trip, not equality with the sparse input dict.
        body = "## Goal\ng1\n\n## Notes\n- n1\n"
        sections = T.split_sections(body)
        rendered = T.render_body(sections)
        resplit = T.split_sections(rendered)
        self.assertEqual(resplit["Goal"], "g1")
        self.assertEqual(resplit["Notes"], "- n1")
        for name in T.SECTIONS:
            self.assertIn(name, resplit)
        self.assertEqual(T.render_body(resplit), rendered)


class TicketSectionMethodTests(unittest.TestCase):
    def test_set_section(self):
        tk = make_ticket()
        tk.set_section("goal", "New goal")
        self.assertEqual(tk.section("goal"), "New goal")

    def test_set_section_by_canonical_name(self):
        tk = make_ticket()
        tk.set_section("Goal", "New goal")
        self.assertEqual(tk.section("goal"), "New goal")

    def test_append_to_section_when_empty(self):
        tk = make_ticket()
        tk.append_to_section("goal", "first")
        self.assertEqual(tk.section("goal"), "first")

    def test_append_to_section_when_present(self):
        tk = make_ticket()
        tk.set_section("goal", "first")
        tk.append_to_section("goal", "second")
        self.assertEqual(tk.section("goal"), "first\n\nsecond")

    def test_append_note_single_line(self):
        tk = make_ticket()
        tk.append_note("claude.opus.001", "claimed", at="2026-09-13T01:00:00")
        self.assertEqual(tk.section("notes"), "- 2026-09-13T01:00:00 claude.opus.001: claimed")

    def test_append_note_multi_line_indented(self):
        tk = make_ticket()
        tk.append_note("claude.opus.001", "line one\nline two", at="2026-09-13T01:00:00")
        expected = "- 2026-09-13T01:00:00 claude.opus.001: line one\n  line two"
        self.assertEqual(tk.section("notes"), expected)

    def test_append_note_twice_produces_two_bullets(self):
        tk = make_ticket()
        tk.append_note("a.b.c", "first", at="2026-09-13T01:00:00")
        tk.append_note("a.b.c", "second", at="2026-09-13T02:00:00")
        notes = tk.section("notes")
        self.assertEqual(notes.count("\n- "), 1)
        self.assertTrue(notes.startswith("- 2026-09-13T01:00:00"))
        self.assertIn("- 2026-09-13T02:00:00 a.b.c: second", notes)


class ParseAndRenderRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_fields(self):
        tk = make_ticket(domain="python", epic="e1", priority=3, tags=["a", "b"],
                          scope=["src/x.py"], depends_on=[])
        tk.set_section("goal", "Do the thing")
        md = tk.to_markdown()
        tk2 = T.parse_ticket(md)
        self.assertEqual(tk2.id, tk.id)
        self.assertEqual(tk2.domain, "python")
        self.assertEqual(tk2.tags, ["a", "b"])
        self.assertEqual(tk2.scope, ["src/x.py"])
        self.assertEqual(tk2.section("goal"), "Do the thing")

    def test_front_matter_boundary_only_at_exact_dashes_line(self):
        # A body containing a line of dashes that is NOT exactly '---' must
        # not be mistaken for the closing boundary.
        md = (
            "---\n"
            "id: rg-00001\n"
            "title: t\n"
            "status: open\n"
            "kind: implement\n"
            "skill: medium\n"
            "---\n\n"
            "## Goal\n"
            "----\n"
            "not a boundary\n"
        )
        tk = T.parse_ticket(md)
        self.assertIn("----", tk.section("goal"))

    def test_quoted_value_containing_triple_dash_survives(self):
        md = (
            "---\n"
            "id: rg-00001\n"
            "title: 'has --- dashes'\n"
            "status: open\n"
            "kind: implement\n"
            "skill: medium\n"
            "---\n\n"
            "## Goal\nx\n"
        )
        tk = T.parse_ticket(md)
        self.assertEqual(tk.title, "has --- dashes")

    def test_no_closing_dashes_is_error(self):
        md = "---\nid: rg-00001\ntitle: t\nstatus: open\nkind: implement\nskill: medium\n\n## Goal\n"
        with self.assertRaises(T.TicketError):
            T.parse_ticket(md)

    def test_missing_leading_dashes_is_error(self):
        with self.assertRaises(T.TicketError):
            T.parse_ticket("id: rg-00001\ntitle: t\n---\n\nbody\n")

    def test_unknown_field_raises(self):
        md = (
            "---\nid: rg-00001\ntitle: t\nstatus: open\nkind: implement\nskill: medium\n"
            "bogus_field: yes\n---\n\nbody\n"
        )
        with self.assertRaises(T.TicketError):
            T.parse_ticket(md)

    def test_missing_required_field_raises(self):
        md = "---\nid: rg-00001\ntitle: t\nstatus: open\nkind: implement\n---\n\nbody\n"
        with self.assertRaises(T.TicketError):
            T.parse_ticket(md)

    def test_non_list_tags_raises(self):
        md = (
            "---\nid: rg-00001\ntitle: t\nstatus: open\nkind: implement\nskill: medium\n"
            "tags: notalist\n---\n\nbody\n"
        )
        with self.assertRaises(T.TicketError):
            T.parse_ticket(md)

    def test_non_list_scope_raises(self):
        md = (
            "---\nid: rg-00001\ntitle: t\nstatus: open\nkind: implement\nskill: medium\n"
            "scope: notalist\n---\n\nbody\n"
        )
        with self.assertRaises(T.TicketError):
            T.parse_ticket(md)


class ValidateTests(unittest.TestCase):
    def test_valid_ticket_has_no_problems(self):
        tk = make_ticket()
        self.assertEqual(T.validate(tk), [])

    def test_bad_id_pattern(self):
        tk = make_ticket(id="not_an_id")
        self.assertTrue(any("is not of the form prefix-hex" in p for p in T.validate(tk)))

    def test_empty_title(self):
        tk = make_ticket(title="   ")
        self.assertTrue(any("title is empty" in p for p in T.validate(tk)))

    def test_title_too_long(self):
        tk = make_ticket(title="x" * (T.TITLE_MAX + 1))
        self.assertTrue(any("longer than" in p for p in T.validate(tk)))

    def test_bad_status(self):
        tk = make_ticket(status="nope")
        self.assertTrue(any("status" in p for p in T.validate(tk)))

    def test_bad_kind(self):
        tk = make_ticket(kind="nope")
        self.assertTrue(any("kind" in p for p in T.validate(tk)))

    def test_bad_skill(self):
        tk = make_ticket(skill="nope")
        self.assertTrue(any("skill" in p for p in T.validate(tk)))

    def test_non_integer_priority(self):
        tk = make_ticket()
        tk.priority = "high"  # type: ignore[assignment]
        self.assertTrue(any("priority must be an integer" in p for p in T.validate(tk)))

    def test_negative_review_rounds(self):
        tk = make_ticket(review_rounds=-1)
        self.assertTrue(any("review_rounds" in p for p in T.validate(tk)))

    def test_bad_verdict(self):
        tk = make_ticket(verdict="maybe")
        self.assertTrue(any("verdict" in p for p in T.validate(tk)))

    def test_bad_assignee_id(self):
        tk = make_ticket(assignee="not a valid id")
        self.assertTrue(any("assignee" in p for p in T.validate(tk)))

    def test_bad_reviewer_id(self):
        tk = make_ticket(reviewer="not a valid id")
        self.assertTrue(any("reviewer" in p for p in T.validate(tk)))

    def test_depends_on_bad_id(self):
        tk = make_ticket(depends_on=["nonsense"])
        self.assertTrue(any("is not a ticket id" in p for p in T.validate(tk)))

    def test_depends_on_self(self):
        tk = make_ticket(depends_on=["rg-00001"])
        self.assertTrue(any("depends on itself" in p for p in T.validate(tk)))

    def test_bad_date_field(self):
        tk = make_ticket(created="not-a-date")
        self.assertTrue(any("created" in p and "YYYY-MM-DD" in p for p in T.validate(tk)))

    def test_in_progress_without_assignee(self):
        tk = make_ticket(status="in_progress")
        self.assertTrue(any("in_progress ticket has no assignee" in p for p in T.validate(tk)))

    def test_in_progress_with_assignee_ok(self):
        tk = make_ticket(status="in_progress", assignee="claude.opus.001")
        self.assertEqual(T.validate(tk), [])

    def test_review_without_assignee(self):
        tk = make_ticket(status="review")
        self.assertTrue(any("review ticket has no assignee" in p for p in T.validate(tk)))

    def test_blocked_without_reason(self):
        tk = make_ticket(status="blocked")
        self.assertTrue(any("blocked ticket has no blocked_by reason" in p for p in T.validate(tk)))

    def test_closed_without_timestamp(self):
        tk = make_ticket(status="closed")
        self.assertTrue(any("closed ticket has no closed timestamp" in p for p in T.validate(tk)))

    def test_closed_timestamp_but_not_closed_status(self):
        tk = make_ticket(status="open", closed="2026-09-13T00:00:00")
        self.assertTrue(any("has a closed timestamp but is not closed" in p for p in T.validate(tk)))

    def test_origin_without_colon(self):
        tk = make_ticket(origin="nocolon")
        self.assertTrue(any("origin must be source:key" in p for p in T.validate(tk)))

    def test_origin_with_colon_ok(self):
        tk = make_ticket(origin="source:key")
        self.assertEqual(T.validate(tk), [])

    def test_scope_absolute_rejected(self):
        tk = make_ticket(scope=["/abs/path"])
        self.assertTrue(any("scope entry" in p for p in T.validate(tk)))

    def test_scope_dotdot_rejected(self):
        tk = make_ticket(scope=["a/../b"])
        self.assertTrue(any("scope entry" in p for p in T.validate(tk)))


class FileIoTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_atomic_creates_file(self):
        path = self.tmpdir / "sub" / "f.md"
        T.write_atomic("hello\n", path)
        self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")

    def test_write_atomic_overwrites(self):
        path = self.tmpdir / "f.md"
        T.write_atomic("one\n", path)
        T.write_atomic("two\n", path)
        self.assertEqual(path.read_text(encoding="utf-8"), "two\n")

    def test_create_exclusive_writes_new_file(self):
        path = self.tmpdir / "f.md"
        T.create_exclusive("content\n", path)
        self.assertEqual(path.read_text(encoding="utf-8"), "content\n")

    def test_create_exclusive_refuses_existing(self):
        path = self.tmpdir / "f.md"
        T.create_exclusive("content\n", path)
        with self.assertRaises(T.TicketError):
            T.create_exclusive("other\n", path)
        # original content untouched
        self.assertEqual(path.read_text(encoding="utf-8"), "content\n")

    def test_move_ticket_changes_folder_and_status(self):
        root = self.tmpdir / ".rungs"
        (root / "open").mkdir(parents=True)
        tk = make_ticket(status="open")
        src = root / "open" / f"{tk.id}.md"
        T.save_ticket(tk, src)
        dest = T.move_ticket(src, tk, root, "blocked")
        self.assertFalse(src.exists())
        self.assertTrue(dest.exists())
        self.assertEqual(dest.parent.name, "blocked")
        self.assertEqual(tk.status, "blocked")
        reloaded = T.load_ticket(dest)
        self.assertEqual(reloaded.status, "blocked")

    def test_move_ticket_to_closed_uses_month_folder(self):
        root = self.tmpdir / ".rungs"
        (root / "review").mkdir(parents=True)
        tk = make_ticket(status="review", assignee="claude.opus.001")
        src = root / "review" / f"{tk.id}.md"
        T.save_ticket(tk, src)
        tk.closed = "2026-09-13T12:00:00"
        dest = T.move_ticket(src, tk, root, "closed")
        self.assertEqual(dest, root / "closed" / "2026-09" / f"{tk.id}.md")
        self.assertTrue(dest.exists())

    def test_claim_file_moves_and_sets_assignee(self):
        root = self.tmpdir / ".rungs"
        (root / "open").mkdir(parents=True)
        tk = make_ticket(status="open")
        src = root / "open" / f"{tk.id}.md"
        T.save_ticket(tk, src)
        dest = T.claim_file(src, tk, root, "claude.opus.001")
        self.assertEqual(dest, root / "in_progress" / f"{tk.id}.md")
        self.assertFalse(src.exists())
        self.assertEqual(tk.status, "in_progress")
        self.assertEqual(tk.assignee, "claude.opus.001")

    def test_claim_file_second_claim_raises(self):
        root = self.tmpdir / ".rungs"
        (root / "open").mkdir(parents=True)
        (root / "in_progress").mkdir(parents=True)
        tk = make_ticket(status="open")
        src = root / "open" / f"{tk.id}.md"
        T.save_ticket(tk, src)
        # simulate a race: someone else already created the destination file
        (root / "in_progress" / f"{tk.id}.md").write_text("already claimed\n", encoding="utf-8")
        with self.assertRaises(T.TicketError):
            T.claim_file(src, tk, root, "claude.opus.001")
        # source ticket must still be there; the race did not destroy it
        self.assertTrue(src.exists())


class FindCyclesTests(unittest.TestCase):
    def _mk(self, tid, deps):
        return make_ticket(id=tid, depends_on=deps)

    def test_no_cycle(self):
        by_id = {"a": self._mk("a", ["b"]), "b": self._mk("b", [])}
        self.assertEqual(T.find_cycles(by_id), [])

    def test_simple_two_cycle(self):
        by_id = {"a": self._mk("a", ["b"]), "b": self._mk("b", ["a"])}
        cycles = T.find_cycles(by_id)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]), {"a", "b"})

    def test_self_loop(self):
        by_id = {"a": self._mk("a", ["a"])}
        cycles = T.find_cycles(by_id)
        self.assertEqual(cycles, [["a"]])

    def test_disjoint_components_one_cycle(self):
        by_id = {
            "a": self._mk("a", ["b"]), "b": self._mk("b", ["a"]),
            "c": self._mk("c", []), "d": self._mk("d", ["c"]),
        }
        cycles = T.find_cycles(by_id)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]), {"a", "b"})

    def test_missing_dependency_is_not_a_cycle(self):
        by_id = {"a": self._mk("a", ["ghost"])}
        self.assertEqual(T.find_cycles(by_id), [])


if __name__ == "__main__":
    unittest.main()
