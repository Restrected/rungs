"""Tests for rungs.store: locate the store, iterate/find tickets, workable
set, ordering, events. See DESIGN.md sections 2 and 5-6."""

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from helpers import RungsTestCase

from rungs.roster import Agent
from rungs.store import STATUS_FOLDERS, EXTRA_FOLDERS, Store, StoreError
from rungs import ticket as T


def mk(id, status="open", skill="medium", priority=None, epic=None, domain=None,
       created="2026-09-13T00:00:00", **kw):
    return T.Ticket(
        id=id, title=f"title {id}", status=status, kind="implement", skill=skill,
        created=created, updated=created, priority=priority, epic=epic, domain=domain, **kw
    )


class LocateTests(RungsTestCase):
    """Store.locate walks up to the nearest .rungs/; Store.require raises when none."""

    def test_find_returns_none_when_no_store(self):
        self.assertIsNone(Store.locate(self.project))

    def test_require_raises_when_no_store(self):
        with self.assertRaises(StoreError):
            Store.require(self.project)

    def test_find_locates_store_at_root(self):
        self.init_store()
        found = Store.locate(self.project)
        self.assertIsNotNone(found)
        self.assertEqual(found.root, self.project / ".rungs")

    def test_find_walks_up_from_subdirectory(self):
        self.init_store()
        sub = self.project / "a" / "b" / "c"
        sub.mkdir(parents=True)
        found = Store.locate(sub)
        self.assertIsNotNone(found)
        self.assertEqual(found.root, self.project / ".rungs")

    def test_require_walks_up_from_subdirectory(self):
        self.init_store()
        sub = self.project / "a" / "b"
        sub.mkdir(parents=True)
        found = Store.require(sub)
        self.assertEqual(found.root, self.project / ".rungs")


class EnsureLayoutTests(RungsTestCase):
    def test_creates_every_folder(self):
        root = self.project / ".rungs"
        store = Store(root)
        created = store.ensure_layout()
        self.assertTrue(created)  # something was created
        for name in STATUS_FOLDERS + EXTRA_FOLDERS:
            with self.subTest(name=name):
                self.assertTrue((root / name).is_dir())

    def test_idempotent(self):
        root = self.project / ".rungs"
        store = Store(root)
        store.ensure_layout()
        created_second_time = store.ensure_layout()
        self.assertEqual(created_second_time, [])


class CreateAndFindTests(RungsTestCase):
    def test_create_writes_file_in_status_folder(self):
        store = self.init_store()
        path = store.create(mk("rg-00001", status="open"))
        self.assertEqual(path, store.root / "open" / "rg-00001.md")
        self.assertTrue(path.is_file())

    def test_create_sets_timestamps_when_absent(self):
        store = self.init_store()
        tk = mk("rg-00001", status="open", created="")
        store.create(tk)
        self.assertNotEqual(tk.created, "")
        self.assertEqual(tk.created, tk.updated)

    def test_create_refuses_duplicate(self):
        store = self.init_store()
        store.create(mk("rg-00001", status="open"))
        with self.assertRaises(T.TicketError):
            store.create(mk("rg-00001", status="open"))

    def test_find_exact_match(self):
        store = self.init_store()
        store.create(mk("rg-aaaa1"))
        store.create(mk("rg-aaaa2"))
        _, ticket = store.find("rg-aaaa1")
        self.assertEqual(ticket.id, "rg-aaaa1")

    def test_find_substring_match(self):
        store = self.init_store()
        store.create(mk("rg-aaaa1"))
        _, ticket = store.find("aaaa1")
        self.assertEqual(ticket.id, "rg-aaaa1")

    def test_find_ambiguous_substring_without_unique_returns_first_sorted(self):
        store = self.init_store()
        store.create(mk("rg-aaaa1"))
        store.create(mk("rg-aaaa2"))
        _, ticket = store.find("aaaa")
        self.assertEqual(ticket.id, "rg-aaaa1")

    def test_find_ambiguous_substring_with_unique_raises(self):
        store = self.init_store()
        store.create(mk("rg-aaaa1"))
        store.create(mk("rg-aaaa2"))
        with self.assertRaises(StoreError):
            store.find("aaaa", unique=True)

    def test_find_no_match_raises(self):
        store = self.init_store()
        with self.assertRaises(StoreError):
            store.find("nonexistent")

    def test_find_empty_term_raises(self):
        store = self.init_store()
        with self.assertRaises(StoreError):
            store.find("   ")

    def test_find_by_origin(self):
        store = self.init_store()
        store.create(mk("rg-00001", origin="helpdesk:req-1"))
        found = store.find_by_origin("helpdesk:req-1")
        self.assertIsNotNone(found)
        self.assertEqual(found[1].id, "rg-00001")

    def test_find_by_origin_none(self):
        store = self.init_store()
        store.create(mk("rg-00001"))
        self.assertIsNone(store.find_by_origin("nope:nope"))

    def test_existing_ids(self):
        store = self.init_store()
        store.create(mk("rg-00001"))
        store.create(mk("rg-00002"))
        self.assertEqual(store.existing_ids(), {"rg-00001", "rg-00002"})

    def test_new_id_is_unique_and_valid(self):
        store = self.init_store()
        store.create(mk("rg-00001"))
        new_id = store.new_id()
        self.assertNotIn(new_id, store.existing_ids())
        self.assertRegex(new_id, r"^rg-[0-9a-f]{5}$")


class WorkableAndOrderingTests(RungsTestCase):
    def test_workable_excludes_non_open(self):
        store = self.init_store()
        by_id = {"a": mk("a", status="triage")}
        self.assertEqual(store.workable(by_id.values(), by_id), [])

    def test_workable_open_no_deps(self):
        by_id = {"a": mk("a", status="open")}
        result = Store.workable(by_id.values(), by_id)
        self.assertEqual([t.id for t in result], ["a"])

    def test_workable_blocked_by_open_dependency(self):
        by_id = {
            "a": mk("a", status="open", depends_on=["b"]),
            "b": mk("b", status="open"),
        }
        result = Store.workable(by_id.values(), by_id)
        self.assertEqual([t.id for t in result], ["b"])

    def test_workable_unblocked_when_dependency_closed(self):
        by_id = {
            "a": mk("a", status="open", depends_on=["b"]),
            "b": mk("b", status="closed", closed="2026-09-13T00:00:00"),
        }
        result = Store.workable(by_id.values(), by_id)
        self.assertEqual([t.id for t in result], ["a"])

    def test_workable_missing_dependency_counts_as_unmet(self):
        by_id = {"a": mk("a", status="open", depends_on=["ghost"])}
        self.assertEqual(Store.workable(by_id.values(), by_id), [])

    def test_order_for_agent_own_level_first(self):
        # order_for_agent's contract (own level first, then descending) is
        # documented for the at-or-below-rank candidates next_for already
        # filters to; give it exactly that (no ticket above the agent's rank).
        agent = Agent(id="a.b.c", skill="medium")
        tickets = [mk("lo", skill="low"), mk("mid", skill="medium")]
        ordered = Store.order_for_agent(tickets, agent)
        self.assertEqual(ordered[0].id, "mid")

    def test_order_for_agent_descending_by_level(self):
        agent = Agent(id="a.b.c", skill="high")
        tickets = [mk("lo", skill="low"), mk("med", skill="medium"), mk("vlo", skill="very-low")]
        ordered = Store.order_for_agent(tickets, agent)
        self.assertEqual([t.id for t in ordered], ["med", "lo", "vlo"])

    def test_order_for_agent_priority_then_age(self):
        agent = Agent(id="a.b.c", skill="medium")
        t1 = mk("newer", skill="medium", priority=1, created="2026-09-13T02:00:00")
        t2 = mk("older", skill="medium", priority=1, created="2026-09-13T01:00:00")
        t3 = mk("urgent", skill="medium", priority=0, created="2026-09-13T03:00:00")
        ordered = Store.order_for_agent([t1, t2, t3], agent)
        self.assertEqual([t.id for t in ordered], ["urgent", "older", "newer"])

    def test_order_for_agent_unset_priority_sorts_last(self):
        agent = Agent(id="a.b.c", skill="medium")
        t1 = mk("has_pri", skill="medium", priority=5)
        t2 = mk("no_pri", skill="medium", priority=None)
        ordered = Store.order_for_agent([t2, t1], agent)
        self.assertEqual([t.id for t in ordered], ["has_pri", "no_pri"])


class NextForTests(RungsTestCase):
    def setUp(self):
        super().setUp()
        self.store = self.init_store()

    def test_excludes_above_rank(self):
        self.store.create(mk("rg-00001", skill="xhigh"))
        agent = Agent(id="a.b.c", skill="medium")
        tickets, below = self.store.next_for(agent, count=10)
        self.assertEqual(tickets, [])
        self.assertEqual(below, 0)

    def test_floor_excludes_by_default_and_counts_below(self):
        self.store.create(mk("rg-00001", skill="high", priority=1))
        self.store.create(mk("rg-00002", skill="low", priority=1))
        agent = Agent(id="a.b.c", skill="high", floor="medium")
        tickets, below = self.store.next_for(agent, count=10)
        self.assertEqual([t.id for t in tickets], ["rg-00001"])
        self.assertEqual(below, 1)

    def test_ignore_floor_includes_everything(self):
        self.store.create(mk("rg-00001", skill="high", priority=1))
        self.store.create(mk("rg-00002", skill="low", priority=1))
        agent = Agent(id="a.b.c", skill="high", floor="medium")
        tickets, below = self.store.next_for(agent, count=10, ignore_floor=True)
        self.assertEqual({t.id for t in tickets}, {"rg-00001", "rg-00002"})
        self.assertEqual(below, 1)

    def test_epic_filter(self):
        self.store.create(mk("rg-00001", epic="e1"))
        self.store.create(mk("rg-00002", epic="e2"))
        agent = Agent(id="a.b.c", skill="medium")
        tickets, _ = self.store.next_for(agent, count=10, epic="e1")
        self.assertEqual([t.id for t in tickets], ["rg-00001"])

    def test_domain_filter(self):
        self.store.create(mk("rg-00001", domain="python"))
        self.store.create(mk("rg-00002", domain="ui"))
        agent = Agent(id="a.b.c", skill="medium")
        tickets, _ = self.store.next_for(agent, count=10, domain="ui")
        self.assertEqual([t.id for t in tickets], ["rg-00002"])

    def test_count_limits_results(self):
        self.store.create(mk("rg-00001", priority=1))
        self.store.create(mk("rg-00002", priority=2))
        agent = Agent(id="a.b.c", skill="medium")
        tickets, _ = self.store.next_for(agent, count=1)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].id, "rg-00001")


class TopologicalTests(RungsTestCase):
    def test_dependencies_come_first(self):
        store = self.init_store()
        a = mk("a", depends_on=["b"])
        b = mk("b", depends_on=["c"])
        c = mk("c")
        ordered = store.topological([a, b, c])
        self.assertEqual([t.id for t in ordered], ["c", "b", "a"])

    def test_stable_without_dependencies(self):
        store = self.init_store()
        a = mk("a")
        b = mk("b")
        ordered = store.topological([a, b])
        self.assertEqual([t.id for t in ordered], ["a", "b"])

    def test_ignores_dependency_outside_the_given_set(self):
        store = self.init_store()
        a = mk("a", depends_on=["ghost"])
        ordered = store.topological([a])
        self.assertEqual([t.id for t in ordered], ["a"])


class EventsTests(RungsTestCase):
    def test_log_and_read_events(self):
        store = self.init_store()
        store.log_event("claim", "rg-00001", "claude.opus.001", "open", "in_progress")
        events = store.read_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, "claim")
        self.assertEqual(events[0].ticket, "rg-00001")
        self.assertEqual(events[0].from_status, "open")
        self.assertEqual(events[0].to_status, "in_progress")

    def test_read_events_no_file_returns_empty(self):
        store = self.init_store()
        self.assertEqual(store.read_events(), [])

    def test_read_events_filters_by_ticket(self):
        store = self.init_store()
        store.log_event("claim", "rg-00001", "a.b.c")
        store.log_event("claim", "rg-00002", "a.b.c")
        events = store.read_events(ticket_id="rg-00001")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].ticket, "rg-00001")

    def test_read_events_filters_by_since(self):
        store = self.init_store()
        events_path = store.events_path
        events_path.write_text(
            '{"at": "2026-09-13T01:00:00", "agent": "a", "action": "claim", "ticket": "rg-1", "from": null, "to": null, "detail": null}\n'
            '{"at": "2026-09-13T03:00:00", "agent": "a", "action": "note", "ticket": "rg-1", "from": null, "to": null, "detail": null}\n',
            encoding="utf-8",
        )
        events = store.read_events(since="2026-09-13T02:00:00")
        self.assertEqual([e.action for e in events], ["note"])

    def test_read_events_count_limits_to_most_recent(self):
        store = self.init_store()
        store.log_event("one", "rg-1", "a.b.c")
        store.log_event("two", "rg-1", "a.b.c")
        store.log_event("three", "rg-1", "a.b.c")
        events = store.read_events(count=2)
        self.assertEqual([e.action for e in events], ["two", "three"])

    def test_last_event_at(self):
        store = self.init_store()
        store.events_path.write_text(
            '{"at": "2026-09-13T01:00:00", "agent": "a", "action": "claim", "ticket": "rg-1"}\n'
            '{"at": "2026-09-13T02:00:00", "agent": "a", "action": "note", "ticket": "rg-1"}\n'
            '{"at": "2026-09-13T01:30:00", "agent": "a", "action": "claim", "ticket": "rg-2"}\n',
            encoding="utf-8",
        )
        last = store.last_event_at()
        self.assertEqual(last["rg-1"], "2026-09-13T02:00:00")
        self.assertEqual(last["rg-2"], "2026-09-13T01:30:00")


class StaleTests(RungsTestCase):
    def test_stale_ticket_reported(self):
        store = self.init_store()
        old = (datetime.now() - timedelta(hours=48)).isoformat(timespec="seconds")
        # mk() sets both created and updated to the same timestamp.
        store.create(mk("rg-00001", status="in_progress", assignee="claude.opus.001", created=old))
        result = store.stale(24)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0].id, "rg-00001")
        self.assertGreaterEqual(result[0][2], 24)

    def test_recent_ticket_not_stale(self):
        store = self.init_store()
        recent = datetime.now().isoformat(timespec="seconds")
        store.create(mk("rg-00001", status="in_progress", assignee="claude.opus.001", created=recent))
        result = store.stale(24)
        self.assertEqual(result, [])

    def test_non_live_status_ignored(self):
        store = self.init_store()
        old = (datetime.now() - timedelta(hours=100)).isoformat(timespec="seconds")
        store.create(mk("rg-00001", status="open", created=old))
        result = store.stale(24)
        self.assertEqual(result, [])


class StrayFilesTests(RungsTestCase):
    def test_detects_temp_file(self):
        store = self.init_store()
        (store.root / "open" / f"{T.TMP_PREFIX}xyz").write_text("junk", encoding="utf-8")
        strays = store.stray_files()
        self.assertEqual(len(strays), 1)
        self.assertTrue(strays[0].name.startswith(T.TMP_PREFIX))

    def test_detects_non_md_file(self):
        store = self.init_store()
        (store.root / "open" / "notes.txt").write_text("junk", encoding="utf-8")
        strays = store.stray_files()
        self.assertEqual([p.name for p in strays], ["notes.txt"])

    def test_normal_ticket_not_stray(self):
        store = self.init_store()
        store.create(mk("rg-00001", status="open"))
        self.assertEqual(store.stray_files(), [])

    def test_month_folders_under_closed_not_stray(self):
        store = self.init_store()
        tk = mk("rg-00001", status="closed", closed="2026-09-13T00:00:00")
        store.create(tk)
        self.assertEqual(store.stray_files(), [])


class LoadAllLenientTests(RungsTestCase):
    def test_broken_file_reported_as_error_not_raised(self):
        store = self.init_store()
        store.create(mk("rg-00001", status="open"))
        broken_path = store.root / "open" / "rg-00002.md"
        broken_path.write_text("not a valid ticket file at all", encoding="utf-8")
        items, errors = store.load_all_lenient()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][1].id, "rg-00001")
        self.assertEqual(len(errors), 1)
        self.assertIn("rg-00002.md", errors[0])

    def test_no_broken_files(self):
        store = self.init_store()
        store.create(mk("rg-00001", status="open"))
        items, errors = store.load_all_lenient()
        self.assertEqual(len(items), 1)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
