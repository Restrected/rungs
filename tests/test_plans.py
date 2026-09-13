"""Plan files: the grammar of DESIGN.md section 8, and `cut`."""

from __future__ import annotations

import json
import unittest

from helpers import RungsTestCase

DIRECTOR = "claude.fable.001"
OPUS = "claude.opus.001"

FRONT = """---
slug: {slug}
title: 'A plan'
status: draft
owner: claude.fable.001
epic: {epic}
created: '2026-09-13T02:00:00'
---

## Goal

Why this plan exists.

## Tickets

"""

PAIR = """### core — Core modules
- kind: implement
- skill: high
- domain: python
- priority: 2
- tags: core, engine
- scope: src/core.py

#### Goal
Write the core.

#### Decisions already made
No new dependencies.

#### Acceptance criteria
- the tests pass

### cli: The command line
- kind: implement
- skill: medium
- depends_on: core
- scope: src/cli.py

#### Goal
Wire the commands up.

#### Acceptance criteria
- every command has help
"""


class PlansTest(RungsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = self.init_store()

    def write_plan(self, body: str, slug: str = "demo", epic: str = "demo") -> str:
        folder = self.project / ".rungs" / "plans"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{slug}.md").write_text(FRONT.format(slug=slug, epic=epic) + body, encoding="utf-8")
        return slug

    def tickets(self):
        code, out, err = self.run_cli("list", "--json")
        return [] if code == 2 else json.loads(out)

    # -- plan new ---------------------------------------------------------

    def test_plan_new_scaffolds_a_file_whose_example_is_not_parsed(self):
        code, out, err = self.run_cli("plan", "new", "rungs-v1", "--agent", DIRECTOR,
                                      "--title", "Build rungs", "--epic", "rungs-core")
        self.assertEqual(code, 0, err)
        text = self.read(".rungs/plans/rungs-v1.md")
        for heading in ("## Goal", "## Architecture", "## Decisions", "## Tickets"):
            self.assertIn(heading, text)
        self.assertIn("<!--", text)
        self.assertIn("### example-key — One line title", text)
        self.assertIn("status: draft", text)
        # The example lives in an HTML comment, so there is nothing to cut yet.
        code, out, err = self.run_cli("cut", "rungs-v1", "--agent", DIRECTOR)
        self.assertEqual(code, 1)
        self.assertIn("lists no tickets", err)
        self.assertEqual(self.tickets(), [])

    def test_plan_new_refuses_a_bad_slug_and_an_existing_file(self):
        code, out, err = self.run_cli("plan", "new", "Not A Slug", "--agent", DIRECTOR, "--title", "x")
        self.assertEqual(code, 1)
        self.assertIn("must match", err)
        self.run_cli("plan", "new", "twice", "--agent", DIRECTOR, "--title", "x")
        code, out, err = self.run_cli("plan", "new", "twice", "--agent", DIRECTOR, "--title", "x")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    # -- cut --------------------------------------------------------------

    def test_cut_creates_a_dependent_pair_and_skips_them_next_time(self):
        self.write_plan(PAIR)
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR, "--json")
        self.assertEqual(code, 0, err)
        created = json.loads(out)["created"]
        self.assertEqual([c["key"] for c in created], ["core", "cli"])
        core, cli = created[0], created[1]
        self.assertEqual(core["skill"], "high")
        self.assertEqual(core["priority"], 2)
        self.assertEqual(core["tags"], ["core", "engine"])
        self.assertEqual(core["scope"], ["src/core.py"])
        self.assertEqual(core["epic"], "demo")          # inherited from the plan
        self.assertEqual(core["plan"], "demo")
        self.assertEqual(core["status"], "open")
        self.assertIn("Write the core.", core["sections"]["Goal"])
        self.assertIn("No new dependencies.", core["sections"]["Decisions already made"])
        self.assertEqual(cli["depends_on"], [core["id"]])

        plan_text = self.read(".rungs/plans/demo.md")
        self.assertIn("## Cut tickets", plan_text)
        self.assertIn(f"| core | {core['id']} |", plan_text)
        self.assertIn("status: cut", plan_text)

        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR)
        self.assertEqual(code, 0, err)
        self.assertIn("already cut", out)
        self.assertEqual(len(self.tickets()), 2)

    def test_cut_creates_only_the_new_blocks_when_a_plan_grows(self):
        self.write_plan(PAIR)
        self.run_cli("cut", "demo", "--agent", DIRECTOR)
        path = self.project / ".rungs" / "plans" / "demo.md"
        text = path.read_text(encoding="utf-8").replace("## Cut tickets", """### docs — Write the guide
- kind: doc
- skill: low
- depends_on: cli

#### Goal
Explain it.

#### Acceptance criteria
- a reader can follow it

## Cut tickets""")
        path.write_text(text, encoding="utf-8")
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR, "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual([c["key"] for c in data["created"]], ["docs"])
        self.assertEqual(sorted(data["skipped"]), ["cli", "core"])
        cli_id = [t["id"] for t in self.tickets() if t["title"] == "The command line"][0]
        self.assertEqual(data["created"][0]["depends_on"], [cli_id])
        self.assertEqual(len(self.tickets()), 3)

    def test_cut_rejects_an_unknown_depends_on_key_and_creates_nothing(self):
        self.write_plan(PAIR.replace("- depends_on: core", "- depends_on: nowhere"))
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR)
        self.assertEqual(code, 1)
        self.assertIn("depends_on 'nowhere' is neither a key in this plan nor a ticket id", err)
        self.assertIn("nothing was created", err)
        self.assertEqual(self.tickets(), [])
        self.assertNotIn("## Cut tickets", self.read(".rungs/plans/demo.md"))

    def test_cut_accepts_a_dependency_on_an_existing_ticket_and_rejects_a_missing_one(self):
        existing = self.run_cli("create", "--agent", DIRECTOR, "--title", "already here",
                                "--kind", "chore", "--skill", "low")[1].strip()
        self.write_plan(PAIR.replace("- depends_on: core", f"- depends_on: core, {existing}"))
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR, "--json")
        self.assertEqual(code, 0, err)
        cli = json.loads(out)["created"][1]
        self.assertIn(existing, cli["depends_on"])
        self.assertEqual(len(cli["depends_on"]), 2)

        self.write_plan(PAIR.replace("- depends_on: core", "- depends_on: rg-00000"), slug="other")
        code, out, err = self.run_cli("cut", "other", "--agent", DIRECTOR)
        self.assertEqual(code, 1)
        self.assertIn("not a ticket in this store", err)

    def test_cut_dry_run_changes_nothing(self):
        self.write_plan(PAIR)
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("core", out)
        self.assertIn("(new: core)", out)
        self.assertEqual(self.tickets(), [])
        self.assertNotIn("## Cut tickets", self.read(".rungs/plans/demo.md"))
        self.assertIn("status: draft", self.read(".rungs/plans/demo.md"))

    def test_cut_reports_every_grammar_error_with_a_line_number(self):
        self.write_plan("""### core — Core modules
- kind: implement
- skill: medium
- nonsense: yes

#### Goal
g

#### Acceptance criteria
- a

### core — The same key again
- kind: implement
- skill: medium

#### Goal
g

#### Acceptance criteria
- a

### missing-bits — No fields at all

#### Notes on nothing
text

### bad key here — Something
- kind: chore
""")
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR)
        self.assertEqual(code, 1)
        self.assertIn("unknown field 'nonsense'", err)
        self.assertIn("duplicate ticket key 'core'", err)
        self.assertIn("missing required field kind", err)
        self.assertIn("missing required field skill", err)
        self.assertIn("unknown section 'Notes on nothing'", err)
        self.assertIn("expected '### <key> — <title>'", err)
        self.assertIn("the '#### Goal' section is missing or empty", err)
        self.assertIn("the '#### Acceptance criteria' section is missing or empty", err)
        self.assertRegex(err, r"line \d+")
        self.assertEqual(self.tickets(), [])

    def test_cut_rejects_bad_values_and_dependency_cycles(self):
        self.write_plan("""### a — First
- kind: implement
- skill: enormous
- priority: soon
- depends_on: b
- scope: /etc/passwd

#### Goal
g

#### Acceptance criteria
- a

### b — Second
- kind: nonsense
- skill: low
- depends_on: a

#### Goal
g

#### Acceptance criteria
- a
""")
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR)
        self.assertEqual(code, 1)
        self.assertIn("skill 'enormous' is not one of", err)
        self.assertIn("priority 'soon' must be an integer", err)
        self.assertIn("kind 'nonsense' is not one of", err)
        self.assertIn("must be relative to the project root", err)
        self.assertIn("dependency cycle between plan keys", err)
        self.assertEqual(self.tickets(), [])

    def test_a_plan_without_a_tickets_heading_is_an_error(self):
        folder = self.project / ".rungs" / "plans"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "empty.md").write_text(
            "---\nslug: empty\ntitle: 'x'\nstatus: draft\n---\n\n## Goal\n\nnothing\n", encoding="utf-8")
        code, out, err = self.run_cli("cut", "empty", "--agent", DIRECTOR)
        self.assertEqual(code, 1)
        self.assertIn("no '## Tickets' heading", err)

    def test_a_missing_plan_and_a_plan_path_both_work(self):
        code, out, err = self.run_cli("cut", "nope", "--agent", DIRECTOR)
        self.assertEqual(code, 1)
        self.assertIn("no plan 'nope'", err)
        self.write_plan(PAIR)
        code, out, err = self.run_cli("cut", ".rungs/plans/demo.md", "--agent", DIRECTOR)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.tickets()), 2)

    def test_a_fenced_block_is_copied_verbatim(self):
        self.write_plan("""### one — Only one
- kind: doc
- skill: low

#### Goal
Like this:

```
### not a heading
- not: a field
```

#### Acceptance criteria
- a
""")
        code, out, err = self.run_cli("cut", "demo", "--agent", DIRECTOR, "--json")
        self.assertEqual(code, 0, err)
        created = json.loads(out)["created"]
        self.assertEqual(len(created), 1)
        goal = created[0]["sections"]["Goal"]
        self.assertIn("### not a heading", goal)
        self.assertIn("- not: a field", goal)

    def test_a_block_may_override_the_plan_epic(self):
        self.write_plan(PAIR.replace("- domain: python", "- domain: python\n- epic: its-own"))
        data = json.loads(self.run_cli("cut", "demo", "--agent", DIRECTOR, "--json")[1])
        self.assertEqual(data["created"][0]["epic"], "its-own")
        self.assertEqual(data["created"][1]["epic"], "demo")

    def test_cut_is_recorded_in_the_event_log(self):
        self.write_plan(PAIR)
        self.run_cli("cut", "demo", "--agent", DIRECTOR)
        events = json.loads(self.run_cli("log", "--json")[1])
        cuts = [e for e in events if e["action"] == "cut"]
        self.assertEqual(len(cuts), 2)
        self.assertEqual(cuts[0]["agent"], DIRECTOR)
        self.assertEqual(cuts[0]["detail"], "demo:core")

    # -- plan list and show ------------------------------------------------

    def test_plan_list_and_show(self):
        self.assertEqual(self.run_cli("plan", "list")[0], 2)
        self.write_plan(PAIR)
        code, out, err = self.run_cli("plan", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("demo", out)
        self.assertIn("draft", out)
        data = json.loads(self.run_cli("plan", "list", "--json")[1])
        self.assertEqual(data[0]["tickets"], 2)
        self.assertEqual(data[0]["cut"], 0)

        code, out, err = self.run_cli("plan", "show", "demo")
        self.assertEqual(code, 0, err)
        self.assertIn("### core — Core modules", out)
        data = json.loads(self.run_cli("plan", "show", "demo", "--json")[1])
        self.assertEqual([b["key"] for b in data["tickets"]], ["core", "cli"])
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["epic"], "demo")

    def test_plan_show_warns_about_the_problems_cut_would_find(self):
        self.write_plan(PAIR.replace("- skill: high", "- skill: enormous"))
        code, out, err = self.run_cli("plan", "show", "demo")
        self.assertEqual(code, 0, err)
        self.assertIn("1 problem(s) would stop `rungs cut`", err)
        self.assertIn("enormous", err)

    def test_plan_with_no_subcommand_prints_its_help(self):
        code, out, err = self.run_cli("plan")
        self.assertEqual(code, 0, err)
        self.assertIn("new", out)
        self.assertIn("show", out)


if __name__ == "__main__":
    unittest.main()
