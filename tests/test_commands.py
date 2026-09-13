"""create, the listing commands, set, depend and the ordering of `next`."""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from helpers import DEFAULT_ROSTER, RungsTestCase

DIRECTOR = "claude.fable.001"
OPUS = "claude.opus.001"          # medium
SONNET = "claude.sonnet.001"      # low
HAIKU = "claude.haiku.001"        # very-low
ASTRA = "openai.astra.001"        # high


class CommandsTest(RungsTestCase):
    """A store with the default roster; `self.make(...)` creates a ticket."""

    def setUp(self) -> None:
        super().setUp()
        self.store = self.init_store()

    def make(self, title="A ticket", kind="implement", skill="medium", agent=DIRECTOR,
             goal="do the thing", acceptance="- it is done", triage=False, **options) -> str:
        argv = ["create", "--agent", agent, "--title", title, "--kind", kind, "--skill", skill]
        if goal is not None:
            argv += ["--goal", goal]
        if acceptance is not None:
            argv += ["--acceptance", acceptance]
        if triage:
            argv.append("--triage")
        for key, value in options.items():
            argv += ["--" + key.replace("_", "-"), str(value)]
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, err)
        return out.strip().splitlines()[-1]

    # -- create -----------------------------------------------------------

    def test_create_writes_every_section_and_field(self):
        ticket_id = self.make(
            title="Intake: turn inbox JSON into tickets", kind="implement", skill="high",
            domain="python", epic="rungs-core", priority="3", tags="intake,inbox",
            scope="src/rungs/intake.py,tests/test_intake.py",
            goal="Outside systems drop JSON and get tickets",
            acceptance="- an invalid file is rejected",
            decisions="the inbox is the only folder outsiders write to",
            constraints="standard library only",
            evidence_required="the unittest summary line",
        )
        self.assertRegex(ticket_id, r"^rg-[0-9a-f]{5}$")
        code, out, err = self.run_cli("show", ticket_id, "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["status"], "open")
        self.assertEqual(data["skill"], "high")
        self.assertEqual(data["priority"], 3)
        self.assertEqual(data["tags"], ["intake", "inbox"])
        self.assertEqual(data["scope"], ["src/rungs/intake.py", "tests/test_intake.py"])
        self.assertIn("Outside systems", data["sections"]["Goal"])
        self.assertIn("standard library only", data["sections"]["Constraints"])
        for heading in ("Goal", "Decisions already made", "Constraints", "Acceptance criteria",
                        "Evidence required", "Evidence", "Review", "Notes"):
            self.assertIn(f"## {heading}", data["body"])

    def test_create_needs_title_kind_and_skill(self):
        code, out, err = self.run_cli("create", "--agent", DIRECTOR, "--title", "no kind")
        self.assertEqual(code, 1)
        self.assertIn("--kind", err)
        self.assertIn("--skill", err)

    def test_create_rejects_an_unknown_skill(self):
        code, out, err = self.run_cli("create", "--agent", DIRECTOR, "--title", "t",
                                      "--kind", "chore", "--skill", "enormous")
        self.assertEqual(code, 1)
        self.assertIn("unknown skill", err)

    def test_create_triage_and_from_json_and_body_file(self):
        (self.project / "req.json").write_text(json.dumps({
            "title": "From a JSON file", "kind": "fix", "skill": "low",
            "tags": ["a", "b"], "scope": ["docs/"], "goal": "g", "acceptance": "- a",
        }), encoding="utf-8")
        ticket_id = self.run_cli("create", "--agent", DIRECTOR, "--from-json", "req.json",
                                 "--triage")[1].strip()
        data = json.loads(self.run_cli("show", ticket_id, "--json")[1])
        self.assertEqual(data["status"], "triage")
        self.assertEqual(data["kind"], "fix")
        self.assertEqual(data["tags"], ["a", "b"])

        (self.project / "body.md").write_text(
            "## Goal\nfrom the body file\n\n## Acceptance criteria\n- checked\n", encoding="utf-8")
        other = self.run_cli("create", "--agent", DIRECTOR, "--title", "body", "--kind", "doc",
                             "--skill", "low", "--body-file", "body.md")[1].strip()
        data = json.loads(self.run_cli("show", other, "--json")[1])
        self.assertEqual(data["sections"]["Goal"], "from the body file")

    def test_create_rejects_unknown_json_keys_and_missing_dependencies(self):
        (self.project / "bad.json").write_text('{"title": "x", "nope": 1}', encoding="utf-8")
        code, out, err = self.run_cli("create", "--agent", DIRECTOR, "--from-json", "bad.json")
        self.assertEqual(code, 1)
        self.assertIn("unknown keys", err)
        code, out, err = self.run_cli("create", "--agent", DIRECTOR, "--title", "x",
                                      "--kind", "chore", "--skill", "low",
                                      "--depends-on", "rg-00000")
        self.assertEqual(code, 1)
        self.assertIn("do not exist", err)

    def test_create_refuses_an_unregistered_agent(self):
        code, out, err = self.run_cli("create", "--agent", "who.ami.001", "--title", "x",
                                      "--kind", "chore", "--skill", "low")
        self.assertEqual(code, 4)
        self.assertIn("not registered", err)

    # -- listing ----------------------------------------------------------

    def test_list_filters_are_anded_and_closed_is_hidden(self):
        a = self.make(title="alpha", skill="low", epic="one", tags="x")
        self.make(title="beta", skill="high", epic="two", domain="ui")
        code, out, err = self.run_cli("list", "--skill", "low", "--epic", "one")
        self.assertEqual(code, 0, err)
        self.assertIn(a, out)
        self.assertNotIn("beta", out)
        self.assertEqual(self.run_cli("list", "--epic", "one", "--domain", "ui")[0], 2)

        self.run_cli("claim", "--agent", SONNET, a)
        self.run_cli("submit", "--agent", SONNET, a, "--summary", "done")
        self.run_cli("review", "--agent", "claude.opus.reviewer", a, "--verdict", "accept")
        self.assertNotIn(a, self.run_cli("list")[1])
        self.assertIn(a, self.run_cli("list", "--all")[1])
        self.assertIn(a, self.run_cli("list", "--status", "closed")[1])

    def test_list_of_an_empty_store_exits_2(self):
        code, out, err = self.run_cli("list", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out), [])

    def test_search_matches_title_tags_and_body(self):
        a = self.make(title="Rewrite the parser", tags="parser")
        self.make(title="Something else", goal="mentions the parser inside the body")
        self.assertEqual(len(json.loads(self.run_cli("search", "parser", "--json")[1])), 2)
        self.assertIn(a, self.run_cli("search", "REWRITE")[1])
        self.assertEqual(self.run_cli("search", "nothing-like-this")[0], 2)

    def test_board_groups_by_status_with_a_skill_badge(self):
        a = self.make(title="claimed one", skill="low")
        self.make(title="waiting one", triage=True)
        self.run_cli("claim", "--agent", SONNET, a)
        code, out, err = self.run_cli("board")
        self.assertEqual(code, 0, err)
        self.assertIn("in_progress (1)", out)
        self.assertIn("triage (1)", out)
        self.assertIn("[low] " + a, out)
        self.assertIn("shelved (0)", out)

    def test_show_prints_the_raw_file(self):
        ticket_id = self.make(title="raw")
        out = self.run_cli("show", ticket_id)[1]
        self.assertTrue(out.startswith("---\n"))
        self.assertIn("## Notes", out)

    def test_deps_and_log_and_stale(self):
        first = self.make(title="first", skill="low")
        second = self.make(title="second", skill="low", depends_on=first)
        out = self.run_cli("deps", second)[1]
        self.assertIn(first, out)
        self.assertIn("WAITING", out)
        self.assertIn(second, self.run_cli("deps", first)[1])
        data = json.loads(self.run_cli("deps", second, "--json")[1])
        self.assertFalse(data["workable"])

        self.run_cli("claim", "--agent", SONNET, first)
        lines = self.run_cli("log", first)[1].strip().splitlines()
        self.assertTrue(any("claim" in line and "open→in_progress" in line for line in lines))
        self.assertEqual(self.run_cli("log", second, "--since", "2999-01-01")[0], 2)

        self.assertIn(first, self.run_cli("stale", "--hours", "0")[1])
        self.assertEqual(self.run_cli("stale", "--hours", "9999")[0], 2)

    def test_agents_and_whoami(self):
        ticket_id = self.make(title="held", skill="low")
        self.run_cli("claim", "--agent", SONNET, ticket_id)
        out = self.run_cli("agents")[1]
        self.assertIn(DIRECTOR, out)
        self.assertIn(ticket_id, out)
        data = json.loads(self.run_cli("whoami", "--agent", SONNET, "--json")[1])
        self.assertEqual(data["skill"], "low")
        self.assertEqual(data["holding"], [ticket_id])
        self.assertFalse(data["can_review"])
        text = self.run_cli("whoami", "--agent", DIRECTOR)[1]
        self.assertIn("xhigh", text)
        self.assertIn("director", text)

    def test_triage_lists_only_triage_tickets(self):
        waiting = self.make(title="needs work", triage=True)
        self.make(title="already open")
        out = self.run_cli("triage")[1]
        self.assertIn(waiting, out)
        self.assertEqual(len(out.strip().splitlines()), 3)

    # -- next -------------------------------------------------------------

    def test_next_offers_own_level_first_then_lower_then_priority(self):
        low = self.make(title="low one", skill="low", priority="9")
        medium = self.make(title="medium one", skill="medium", priority="9")
        high_a = self.make(title="high urgent", skill="high", priority="1")
        high_b = self.make(title="high later", skill="high", priority="5")
        xhigh = self.make(title="xhigh one", skill="xhigh")
        data = json.loads(self.run_cli("next", "--agent", ASTRA, "--count", "10", "--json")[1])
        self.assertEqual([t["id"] for t in data["tickets"]], [high_a, high_b, medium, low])
        self.assertNotIn(xhigh, [t["id"] for t in data["tickets"]])

    def test_next_respects_the_floor_and_says_how_many_sit_below_it(self):
        roster = DEFAULT_ROSTER.replace(
            "  openai.astra.001:\n    skill: high\n    role: executor\n",
            "  openai.astra.001:\n    skill: high\n    role: executor\n    floor: high\n")
        self.init_store(roster)
        self.make(title="a low one", skill="low")
        self.make(title="another low one", skill="medium")
        code, out, err = self.run_cli("next", "--agent", ASTRA, "--count", "5")
        self.assertEqual(code, 2)
        self.assertIn("2 workable tickets sit below your floor", err)
        self.assertIn("--ignore-floor", err)
        code, out, err = self.run_cli("next", "--agent", ASTRA, "--count", "5", "--ignore-floor")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.strip().splitlines()), 4)

    def test_next_claim_takes_what_it_can_and_keeps_going(self):
        # One ticket is already held by someone else, and one of the offered
        # tickets overlaps live work: neither stops the rest of the batch.
        live = self.make(title="live work", skill="medium", scope="src/shared.py")
        self.run_cli("claim", "--agent", OPUS, live)
        held = self.make(title="already taken", skill="low", priority="1")
        self.assertEqual(self.run_cli("claim", "--agent", OPUS, held)[0], 0)
        clashing = self.make(title="overlaps", skill="low", priority="2", scope="src/shared.py")
        free = self.make(title="free", skill="low", priority="3", scope="src/free.py")

        code, out, err = self.run_cli("next", "--agent", SONNET, "--count", "5", "--claim", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["claimed"], 1)
        self.assertEqual([t["id"] for t in data["tickets"]], [free])
        self.assertIn(f"{clashing} not claimed", err)
        self.assertIn("overlaps", err)
        self.assertEqual(json.loads(self.run_cli("show", held, "--json")[1])["assignee"], OPUS)
        self.assertEqual(json.loads(self.run_cli("show", clashing, "--json")[1])["status"], "open")

    def test_next_count_must_be_one_or_more(self):
        self.make(title="one", skill="low")
        # argparse writes usage errors to the process's own stderr.
        for value, message in [("0", "must be 1 or more"), ("-3", "must be 1 or more"),
                               ("lots", "not a whole number")]:
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                code, out, err = self.run_cli("next", "--agent", SONNET, "--count", value)
            self.assertEqual(code, 2, f"--count {value} should be rejected")
            self.assertIn(message, captured.getvalue())
        self.assertEqual(self.run_cli("next", "--agent", SONNET, "--count", "1")[0], 0)

    def test_next_skips_tickets_whose_dependencies_are_open(self):
        first = self.make(title="first", skill="low")
        second = self.make(title="second", skill="low", depends_on=first)
        data = json.loads(self.run_cli("next", "--agent", SONNET, "--count", "5", "--json")[1])
        self.assertEqual([t["id"] for t in data["tickets"]], [first])
        self.assertNotIn(second, out_ids(data))

    # -- set and depend ---------------------------------------------------

    def test_set_changes_fields_and_sections(self):
        ticket_id = self.make(title="before")
        code, out, err = self.run_cli("set", "--agent", DIRECTOR, ticket_id,
                                      "title", "after", "skill", "4", "priority", "7",
                                      "tags", "one, two", "goal", "a better goal")
        self.assertEqual(code, 0, err)
        data = json.loads(self.run_cli("show", ticket_id, "--json")[1])
        self.assertEqual(data["title"], "after")
        self.assertEqual(data["skill"], "high")
        self.assertEqual(data["priority"], 7)
        self.assertEqual(data["tags"], ["one", "two"])
        self.assertEqual(data["sections"]["Goal"], "a better goal")
        self.assertIn("set title=", data["sections"]["Notes"])
        self.run_cli("set", "--agent", DIRECTOR, ticket_id, "priority", "none")
        self.assertIsNone(json.loads(self.run_cli("show", ticket_id, "--json")[1])["priority"])

    def test_set_refuses_every_lifecycle_field(self):
        ticket_id = self.make()
        for field, hint in [("status", "claim"), ("assignee", "reassign"), ("verdict", "review"),
                            ("depends_on", "depend"), ("files", "submit"), ("closed", "close"),
                            ("blocked_by", "block"), ("id", "never changes")]:
            code, out, err = self.run_cli("set", "--agent", DIRECTOR, ticket_id, field, "x")
            self.assertEqual(code, 1, f"{field} should be refused")
            self.assertIn(hint, err)
        code, out, err = self.run_cli("set", "--agent", DIRECTOR, ticket_id, "nonsense", "x")
        self.assertEqual(code, 1)
        self.assertIn("is not a ticket field", err)
        code, out, err = self.run_cli("set", "--agent", DIRECTOR, ticket_id, "title")
        self.assertEqual(code, 1)
        self.assertIn("FIELD VALUE pairs", err)

    def test_set_refuses_a_value_that_would_break_the_ticket(self):
        ticket_id = self.make()
        code, out, err = self.run_cli("set", "--agent", DIRECTOR, ticket_id, "scope", "/etc/passwd")
        self.assertEqual(code, 1)
        self.assertIn("relative", err)

    def test_depend_adds_clears_and_refuses_a_cycle(self):
        first = self.make(title="first")
        second = self.make(title="second")
        self.assertEqual(self.run_cli("depend", "--agent", DIRECTOR, second, "--on", first)[0], 0)
        code, out, err = self.run_cli("depend", "--agent", DIRECTOR, first, "--on", second)
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)
        self.assertEqual(self.run_cli("depend", "--agent", DIRECTOR, second, "--on", second)[0], 1)
        self.run_cli("depend", "--agent", DIRECTOR, second, "--clear")
        self.assertEqual(json.loads(self.run_cli("show", second, "--json")[1])["depends_on"], [])
        code, out, err = self.run_cli("depend", "--agent", DIRECTOR, second)
        self.assertEqual(code, 1)
        self.assertIn("--on", err)

    def test_note_is_attributed_and_logged(self):
        ticket_id = self.make()
        self.run_cli("note", "--agent", OPUS, ticket_id, "waiting", "on", "the", "owner")
        data = json.loads(self.run_cli("show", ticket_id, "--json")[1])
        self.assertIn(f"{OPUS}: waiting on the owner", data["sections"]["Notes"])
        self.assertIn("note", self.run_cli("log", ticket_id)[1])

    def test_the_agent_file_keeps_the_generated_header(self):
        path = self.project / ".rungs" / "agents" / f"{SONNET}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# {SONNET}\n\nSkill level: low. Written by `rungs docs`.\n\n"
            "<!-- end of generated header -->\n\n## Current work\n- none\n",
            encoding="utf-8")
        first = self.make(title="first", skill="low")
        second = self.make(title="second", skill="low")
        self.run_cli("claim", "--agent", SONNET, first)
        self.run_cli("claim", "--agent", SONNET, second)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Written by `rungs docs`.", text)
        self.assertEqual(text.count("<!-- end of generated header -->"), 1)
        self.assertEqual(text.count("## Current work"), 1)
        self.assertIn(f"- {first} (in_progress): first", text)
        self.assertIn(f"- {second} (in_progress): second", text)
        self.run_cli("release", "--agent", SONNET, first, "--reason", "stop")
        self.run_cli("release", "--agent", SONNET, second, "--reason", "stop")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Written by `rungs docs`.", text)
        self.assertIn("- none", text)
        self.assertNotIn(first, text)

    def test_every_command_has_help(self):
        from rungs.cli import Context, build_parser
        ctx = Context(cwd=self.project)
        build_parser(ctx)
        for name, parser in ctx.subparsers.items():
            self.assertTrue(parser.format_help().strip(), f"{name} has no help")
            self.assertTrue(parser.description, f"{name} has no description")


def out_ids(data):
    return [t["id"] for t in data["tickets"]]


if __name__ == "__main__":
    unittest.main()
