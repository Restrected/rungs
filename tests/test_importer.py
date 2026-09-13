"""`rungs import-arbite`: an old arbite store becomes rungs tickets.

The fixtures below are written the way the real store's files are written —
PyYAML block lists at the same indentation as their key, and a long
single-quoted title folded over two lines — because that is exactly what the
strict miniyaml parser cannot read and the lenient fallback must.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import RungsTestCase  # noqa: E402

from rungs import importer  # noqa: E402
from rungs.store import Store  # noqa: E402

# A long title folded over two lines, exactly as PyYAML writes it, plus a
# block list at indent 0 and a closed timestamp.
CLOSED = """---
id: tic-1d94
title: 'Request a change from any page: top-bar button, support ticket with page context,
  arbite export'
status: closed
type: feature
tier: high
domain: fullstack
epic: user-requests
priority: 1
tags:
- support
- change-request
assignee: claude.fable.001
depends_on: []
blocked_by: null
created: '2026-09-12T16:22:18'
updated: '2026-09-13T01:33:27'
closed: '2026-09-13T01:33:27'
---

## Description
A top-bar button files what a user typed as a support ticket.

## Notes
- 2026-09-12T17:05:44 claude.fable.001: built by the director.
"""

OPEN = """---
id: tic-027f
title: 'PDF rendering service: architecture, workflow and integration design'
status: open
type: feature
tier: high
domain: architecture
epic: missing-modules
priority: 29
tags:
- missing-modules
- design
assignee: null
depends_on:
- tic-94e1
- tic-dead
- tic-nope
blocked_by: null
created: '2026-09-12T13:27:32'
updated: '2026-09-12T13:27:32'
closed: null
---

## Description
Design package for the PDF rendering service.

## Analysis
Kept as its own section.

## Notes
"""

DEPENDENCY = """---
id: tic-94e1
title: The programme ticket
status: closed
type: chore
tier: frontier
domain: architecture
epic: missing-modules
priority: 2
tags: []
assignee: null
depends_on: []
blocked_by: null
created: '2026-09-11T10:00:00'
updated: '2026-09-12T10:00:00'
closed: '2026-08-30T10:00:00'
---

## Description
The programme.

## Notes
"""

IN_PROGRESS = """---
id: tic-0196
title: 3.3 Features screen final
status: in_progress
type: feature
tier: medium
domain: ui
epic: phase-3
priority: 4
tags:
- features
assignee: openai.astra.001
depends_on: []
blocked_by: null
created: '2026-09-12T04:45:38'
updated: '2026-09-12T20:41:16'
closed: null
---

## Description
Search, groups, child switches.

## Notes
"""

IN_PROGRESS_STRANGER = """---
id: tic-0197
title: Held by someone who left
status: in_progress
type: bug
tier: low
domain: ui
epic: phase-3
priority: 5
tags: []
assignee: openai.gone.001
depends_on: []
blocked_by: null
created: '2026-09-12T04:45:38'
updated: '2026-09-12T20:41:16'
closed: null
---

## Description
Half done.

## Notes
"""

RAW = """---
id: tic-0aaa
title: 'memo (raw): Requires Classification'
status: raw
type: memo
tier: 'TODO: low|medium|high|frontier'
domain: 'TODO: e.g. mesh, ui, io'
epic: classification
priority: null
tags: []
assignee: null
depends_on: []
blocked_by: null
created: '2026-09-12T09:00:00'
updated: '2026-09-12T09:00:00'
closed: null
---

## Description
Original request: write down how the release runbook works.

## Notes
"""

WISH = """---
id: tic-0bbb
title: A nice to have
status: open
type: wish
tier: low
domain: ui
epic: ideas
priority: 40
tags:
- idea
assignee: null
depends_on: []
blocked_by: null
created: '2026-09-12T09:00:00'
updated: '2026-09-12T09:00:00'
closed: null
---

## Description
Someday.

## Notes
"""

BLOCKED = """---
id: tic-0ccc
title: Waiting on the owner
status: blocked
type: refactor
tier: medium
domain: php
epic: phase-3
priority: 6
tags: []
assignee: null
depends_on: []
blocked_by: null
created: '2026-09-12T09:00:00'
updated: '2026-09-12T09:00:00'
closed: null
---

## Description
Stalled.

## Notes
"""

SHELVED = """---
id: tic-0ddd
title: Parked for now
status: shelved
type: chore
tier: low
domain: io
epic: phase-3
priority: 30
tags: []
assignee: null
depends_on: []
blocked_by: null
created: '2026-09-12T09:00:00'
updated: '2026-09-12T09:00:00'
closed: null
---

## Description
Parked.

## Notes
"""

PLANNING = """---
id: tic-0eee
title: Never imported
status: open
type: chore
tier: low
domain: io
epic: planning
priority: 1
tags: []
assignee: null
depends_on: []
blocked_by: null
created: '2026-09-12T09:00:00'
updated: '2026-09-12T09:00:00'
closed: null
---

## Description
In planning/, which the importer ignores.

## Notes
"""

FIXTURES = {
    "open/tic-027f.md": OPEN,
    "closed/2026-09/tic-1d94.md": CLOSED,
    "closed/2026-08/tic-94e1.md": DEPENDENCY,
    "in_progress/tic-0196.md": IN_PROGRESS,
    "in_progress/tic-0197.md": IN_PROGRESS_STRANGER,
    "raw/tic-0aaa.md": RAW,
    "wishlist/tic-0bbb.md": WISH,
    "blocked/tic-0ccc.md": BLOCKED,
    "shelved/tic-0ddd.md": SHELVED,
    "planning/tic-0eee.md": PLANNING,
    "agents/claude.opus.001.md": "# claude.opus.001\n\nNo ticket claimed yet.\n",
    "agents/openai.astra.001.md": "# openai.astra.001\n\nWorking tic-0196.\n",
    "agents/not a valid id.md": "# nope\n",
}


def digest(root: Path) -> str:
    """A fingerprint of a whole tree, to prove nothing in it was touched."""
    sha = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        sha.update(str(path.relative_to(root)).encode())
        sha.update(path.read_bytes())
    return sha.hexdigest()


class ImporterTestCase(RungsTestCase):
    def setUp(self):
        super().setUp()
        self.store = self.init_store()
        self.arbite = self.project / "legacy" / ".arbite"
        for relative, text in FIXTURES.items():
            path = self.arbite / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def by_id(self):
        return {t.id: t for _p, t in Store(self.project / ".rungs").load_all()}

    def run_import(self, *extra):
        return self.run_cli("import-arbite", str(self.arbite), *extra)


class ReaderTests(ImporterTestCase):
    def test_a_folded_single_quoted_title_is_joined(self):
        doc = importer.parse_arbite(CLOSED)
        self.assertEqual(
            doc.fields["title"],
            "Request a change from any page: top-bar button, support ticket with page "
            "context, arbite export",
        )
        self.assertEqual(doc.fields["tags"], ["support", "change-request"])
        self.assertEqual(doc.fields["closed"], "2026-09-13T01:33:27")

    def test_block_lists_at_the_key_indentation_are_read(self):
        doc = importer.parse_arbite(OPEN)
        self.assertEqual(doc.fields["depends_on"], ["tic-94e1", "tic-dead", "tic-nope"])
        self.assertEqual(doc.fields["priority"], 29)
        self.assertIsNone(doc.fields["assignee"])

    def test_the_boundary_is_a_line_of_exactly_three_dashes(self):
        text = "---\nid: tic-0f0f\ntitle: 'a --- b'\n---\n\n## Description\nx\n"
        doc = importer.parse_arbite(text)
        self.assertEqual(doc.fields["title"], "a --- b")
        self.assertIn("## Description", doc.body)

    def test_a_file_without_front_matter_is_an_error(self):
        with self.assertRaises(importer.ArbiteError):
            importer.parse_arbite("no front matter\n")


class MappingTests(ImporterTestCase):
    def test_every_folder_is_walked_and_planning_is_ignored(self):
        code, out, err = self.run_import()
        self.assertEqual(code, 0, err)
        tickets = self.by_id()
        self.assertEqual(len(tickets), 9)
        self.assertNotIn("tic-0eee", tickets)
        self.assertIn("9 ticket(s) imported", out)

    def test_status_mapping(self):
        self.run_import()
        tickets = self.by_id()
        self.assertEqual(tickets["tic-027f"].status, "open")
        self.assertEqual(tickets["tic-1d94"].status, "closed")
        self.assertEqual(tickets["tic-0196"].status, "in_progress")
        self.assertEqual(tickets["tic-0aaa"].status, "triage")     # raw -> triage
        self.assertEqual(tickets["tic-0bbb"].status, "shelved")    # wishlist folder
        self.assertEqual(tickets["tic-0ccc"].status, "blocked")
        self.assertEqual(tickets["tic-0ddd"].status, "shelved")
        # The folder is the status.
        root = self.project / ".rungs"
        self.assertTrue((root / "open" / "tic-027f.md").is_file())
        self.assertTrue((root / "triage" / "tic-0aaa.md").is_file())
        self.assertTrue((root / "closed" / "2026-09" / "tic-1d94.md").is_file())
        self.assertTrue((root / "closed" / "2026-08" / "tic-94e1.md").is_file())

    def test_tier_becomes_skill(self):
        self.run_import()
        tickets = self.by_id()
        self.assertEqual(tickets["tic-027f"].skill, "high")
        self.assertEqual(tickets["tic-0196"].skill, "medium")
        self.assertEqual(tickets["tic-0bbb"].skill, "low")
        self.assertEqual(tickets["tic-94e1"].skill, "xhigh")       # frontier -> xhigh
        # A TODO placeholder tier falls back to the intake default skill.
        self.assertEqual(tickets["tic-0aaa"].skill, "medium")

    def test_type_becomes_kind(self):
        self.run_import()
        tickets = self.by_id()
        self.assertEqual(tickets["tic-027f"].kind, "implement")    # feature
        self.assertEqual(tickets["tic-0197"].kind, "fix")          # bug
        self.assertEqual(tickets["tic-0ccc"].kind, "implement")    # refactor
        self.assertEqual(tickets["tic-0ddd"].kind, "chore")
        self.assertEqual(tickets["tic-0aaa"].kind, "doc")          # memo
        self.assertEqual(tickets["tic-0bbb"].kind, "implement")    # wish

    def test_the_wishlist_folder_adds_its_tag(self):
        self.run_import()
        self.assertIn("wishlist", self.by_id()["tic-0bbb"].tags)
        self.assertIn("idea", self.by_id()["tic-0bbb"].tags)

    def test_fields_and_body_are_carried_over(self):
        self.run_import()
        ticket = self.by_id()["tic-027f"]
        self.assertEqual(ticket.origin, "arbite:tic-027f")
        self.assertEqual(ticket.epic, "missing-modules")
        self.assertEqual(ticket.domain, "architecture")
        self.assertEqual(ticket.priority, 29)
        self.assertEqual(ticket.tags, ["missing-modules", "design"])
        self.assertIn("Design package for the PDF rendering service.", ticket.section("goal"))
        # An unrecognised heading survives as its own section.
        self.assertIn("Kept as its own section.", ticket.section("Analysis"))
        closed = self.by_id()["tic-1d94"]
        self.assertIn("built by the director", closed.section("notes"))
        self.assertEqual(closed.closed, "2026-09-13T01:33:27")

    def test_an_assignee_in_the_roster_is_kept(self):
        self.run_import()
        ticket = self.by_id()["tic-0196"]
        self.assertEqual(ticket.status, "in_progress")
        self.assertEqual(ticket.assignee, "openai.astra.001")

    def test_an_assignee_not_in_the_roster_is_opened_with_a_note(self):
        _code, out, _err = self.run_import()
        ticket = self.by_id()["tic-0197"]
        self.assertEqual(ticket.status, "open")
        self.assertIsNone(ticket.assignee)
        self.assertIn("openai.gone.001", ticket.section("notes"))
        self.assertIn("is not in rungs.yaml", out)

    def test_a_blocked_ticket_without_a_reason_gets_one(self):
        self.run_import()
        self.assertTrue(self.by_id()["tic-0ccc"].blocked_by)

    def test_dependencies_are_kept_and_dangling_ones_reported(self):
        _code, out, _err = self.run_import()
        ticket = self.by_id()["tic-027f"]
        # tic-nope is not a ticket id at all, so it is dropped; tic-dead is a
        # well-formed id that nothing supplies, so it is kept and reported.
        self.assertEqual(ticket.depends_on, ["tic-94e1", "tic-dead"])
        self.assertIn("depends_on entry 'tic-nope' is not a ticket id", out)
        self.assertIn("depends_on tic-dead does not exist", out)

    def test_one_import_event_per_ticket(self):
        self.run_import()
        events = [e for e in Store(self.project / ".rungs").read_events() if e.action == "import"]
        self.assertEqual(len(events), 9)
        self.assertEqual(
            sorted(e.detail for e in events)[0], "arbite:tic-0196")


class AgentFileTests(ImporterTestCase):
    def test_agent_files_are_copied_below_a_generated_header(self):
        _code, out, _err = self.run_import()
        text = self.read(".rungs/agents/openai.astra.001.md")
        self.assertIn("<!-- end of generated header -->", text)
        self.assertIn("Skill level: **high**", text)
        self.assertIn("Working tic-0196.", text)
        self.assertIn("agent file(s) copied", out)

    def test_an_existing_agent_file_is_not_touched(self):
        path = self.project / ".rungs" / "agents" / "claude.opus.001.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mine, hands off\n", encoding="utf-8")
        self.run_import()
        self.assertEqual(path.read_text(encoding="utf-8"), "mine, hands off\n")

    def test_a_file_that_is_not_a_valid_agent_id_is_refused(self):
        _code, out, _err = self.run_import()
        self.assertIn("not a valid agent id", out)
        self.assertFalse((self.project / ".rungs" / "agents" / "not a valid id.md").exists())


class ModeTests(ImporterTestCase):
    def test_ids_already_in_the_store_are_skipped(self):
        self.run_import()
        _code, out, _err = self.run_import()
        self.assertEqual(len(self.by_id()), 9)
        self.assertIn("9 skipped (already in the store)", out)
        self.assertIn("0 ticket(s) imported", out)

    def test_dry_run_writes_nothing(self):
        code, out, err = self.run_import("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.by_id(), {})
        self.assertIn("dry run: nothing was written", out)
        self.assertIn("tic-027f", out)
        self.assertIn("open/feature/high", out)
        self.assertIn("open/implement/high", out)
        self.assertFalse((self.project / ".rungs" / "agents" / "openai.astra.001.md").exists())
        self.assertFalse((self.project / ".rungs" / "events.jsonl").exists())

    def test_json_output(self):
        _code, out, _err = self.run_import("--json")
        summary = json.loads(out)
        self.assertEqual(len(summary["imported"]), 9)
        self.assertEqual(summary["by_status"]["closed"], 2)
        self.assertEqual(summary["skipped"], [])

    def test_a_missing_store_is_an_error(self):
        code, _out, err = self.run_cli("import-arbite", str(self.project / "nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("no arbite store at", err)

    def test_the_arbite_store_is_never_modified(self):
        before = digest(self.arbite)
        self.run_import()
        self.run_import("--dry-run")
        self.assertEqual(digest(self.arbite), before)


if __name__ == "__main__":
    unittest.main()


class IdValidationTests(ImporterTestCase):
    def test_a_ticket_id_that_is_not_an_id_is_refused(self):
        (self.arbite / "open" / "bad.md").write_text(
            OPEN.replace("id: tic-027f", "id: ../../../etc/passwd"), encoding="utf-8")
        _code, out, _err = self.run_import()
        self.assertIn("is not a usable ticket id", out)
        self.assertNotIn("passwd", str(list((self.project / ".rungs").rglob("*"))))

    def test_an_agent_file_name_that_is_not_an_agent_id_is_refused(self):
        (self.arbite / "agents" / "..md").write_text("# no\n", encoding="utf-8")
        _code, out, _err = self.run_import()
        self.assertIn("not copied", out)
        agents = sorted(p.name for p in (self.project / ".rungs" / "agents").iterdir())
        for name in agents:
            self.assertRegex(name, r"^[a-z0-9]+(\.[a-z0-9-]+){1,3}\.md$")

    def test_copied_agent_files_go_through_store_agent_file(self):
        self.run_import()
        for agent_id in ("openai.astra.001", "claude.opus.001"):
            expected = self.store.agent_file(agent_id)
            self.assertEqual(expected.parent, self.project / ".rungs" / "agents")
            self.assertTrue(expected.is_file())

