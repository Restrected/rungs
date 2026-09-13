"""The lifecycle: the happy path, the return loop, every refusal and the
recovery table of DESIGN.md section 5."""

from __future__ import annotations

import json
import re
import unittest

from helpers import DEFAULT_ROSTER, RungsTestCase

DIRECTOR = "claude.fable.001"     # xhigh, director
REVIEWER = "claude.opus.reviewer" # high, reviewer
ASTRA = "openai.astra.001"        # high, executor
OPUS = "claude.opus.001"          # medium, executor
SONNET = "claude.sonnet.001"      # low, executor
HAIKU = "claude.haiku.001"        # very-low, executor


def roster_with(**policy) -> str:
    """The default roster with one or more policy settings changed."""
    text = DEFAULT_ROSTER
    for key, value in policy.items():
        text, count = re.subn(rf"^  {key}: .*$", f"  {key}: {value}", text, count=1, flags=re.M)
        assert count == 1, f"{key} is not in the default roster"
    return text


class LifecycleTest(RungsTestCase):
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

    def ticket(self, ticket_id) -> dict:
        code, out, err = self.run_cli("show", ticket_id, "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    # -- the happy path ---------------------------------------------------

    def test_create_claim_submit_accept_closes_the_ticket(self):
        ticket_id = self.make(title="Ship the thing", scope="src/a.py,tests/test_a.py")
        self.assertEqual(self.run_cli("claim", "--agent", OPUS, ticket_id)[0], 0)
        data = self.ticket(ticket_id)
        self.assertEqual((data["status"], data["assignee"]), ("in_progress", OPUS))
        self.assertIn(f"{OPUS}: claimed", data["sections"]["Notes"])
        self.assertIn(f"- {ticket_id} (in_progress)", self.read(f".rungs/agents/{OPUS}.md"))

        code, out, err = self.run_cli("submit", "--agent", OPUS, ticket_id,
                                      "--summary", "wrote it and ran the tests",
                                      "--files", "src/a.py,tests/test_a.py",
                                      "--checks", "python3 -m unittest: 12 tests OK")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "review")
        self.assertEqual(data["assignee"], OPUS)
        self.assertEqual(data["files"], ["src/a.py", "tests/test_a.py"])
        self.assertTrue(data["submitted"])
        evidence = data["sections"]["Evidence"]
        self.assertIn("### Summary", evidence)
        self.assertIn("wrote it and ran the tests", evidence)
        self.assertIn("- src/a.py", evidence)
        self.assertIn("12 tests OK", evidence)

        code, out, err = self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "accept")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "closed")
        self.assertEqual(data["verdict"], "accepted")
        self.assertEqual(data["reviewer"], REVIEWER)
        self.assertTrue(data["closed"])
        self.assertIn(f"closed/{data['closed'][:7]}", data["path"])
        self.assertIn("- none", self.read(f".rungs/agents/{OPUS}.md"))
        actions = [e["action"] for e in json.loads(self.run_cli("log", ticket_id, "--json")[1])]
        self.assertEqual(actions, ["create", "claim", "submit", "review"])

    def test_the_return_loop_keeps_the_findings_and_counts_the_rounds(self):
        ticket_id = self.make(title="Needs another pass")
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "first try")
        code, out, err = self.run_cli("review", "--agent", REVIEWER, ticket_id,
                                      "--verdict", "return", "--findings", "the edge case is missing")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "in_progress")
        self.assertEqual(data["assignee"], OPUS)
        self.assertEqual(data["verdict"], "returned")
        self.assertEqual(data["review_rounds"], 1)
        self.assertIsNone(data["submitted"])
        self.assertIn("### Round 1 (", data["sections"]["Review"])
        self.assertIn("the edge case is missing", data["sections"]["Review"])

        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "second try")
        (self.project / "findings.md").write_text("still not right\n", encoding="utf-8")
        self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "return",
                     "--findings-file", "findings.md")
        data = self.ticket(ticket_id)
        self.assertEqual(data["review_rounds"], 2)
        self.assertIn("### Round 1 (", data["sections"]["Review"])
        self.assertIn("### Round 2 (", data["sections"]["Review"])
        self.assertIn("still not right", data["sections"]["Review"])

        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "third try")
        self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "accept")
        self.assertEqual(self.ticket(ticket_id)["status"], "closed")

    def test_review_return_release_sends_it_back_to_open(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "done")
        code, out, err = self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "return",
                                      "--findings", "the author is gone", "--release")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "open")
        self.assertIsNone(data["assignee"])
        self.assertIn("the author is gone", data["sections"]["Review"])

    def test_a_returned_review_needs_findings(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "done")
        code, out, err = self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "return")
        self.assertEqual(code, 1)
        self.assertIn("--findings", err)

    def test_submit_evidence_file_replaces_the_whole_section(self):
        ticket_id = self.make(scope="src/a.py")
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        (self.project / "ev.md").write_text("### What I did\nEverything.\n", encoding="utf-8")
        code, out, err = self.run_cli("submit", "--agent", OPUS, ticket_id,
                                      "--evidence-file", "ev.md", "--files", "src/a.py")
        self.assertEqual(code, 0, err)
        evidence = self.ticket(ticket_id)["sections"]["Evidence"]
        self.assertEqual(evidence.strip(), "### What I did\nEverything.")
        self.assertEqual(self.ticket(ticket_id)["files"], ["src/a.py"])

    def test_evidence_survives_checks_that_contain_a_code_fence(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        checks = "$ cat README\n```\nfenced text\n```\nOK\n$ cat half\n```\nunclosed"
        code, out, err = self.run_cli("submit", "--agent", OPUS, ticket_id,
                                      "--summary", "s", "--checks", checks)
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertIn("fenced text", data["sections"]["Evidence"])
        # The sections after Evidence are still readable, so the fence did not
        # swallow the rest of the file.
        self.assertIn("Review", data["sections"])
        self.assertIn("Notes", data["sections"])
        self.assertIn("submitted for review", data["sections"]["Notes"])
        self.assertEqual(self.run_cli("doctor")[0], 0)

    def test_three_dashes_in_user_text_cannot_break_the_store(self):
        ticket_id = self.make(title="a --- title", goal="before\n---\nafter")
        self.run_cli("note", "--agent", OPUS, ticket_id, "see --- this")
        data = self.ticket(ticket_id)
        self.assertNotIn("---", data["title"])
        self.assertNotIn("\n---\n", data["body"])
        self.assertEqual(self.run_cli("doctor")[0], 0)
        self.assertEqual(self.run_cli("list")[0], 0)

    # -- refusals ---------------------------------------------------------

    def test_claim_above_the_skill_level_is_refused_and_force_writes_a_note(self):
        ticket_id = self.make(title="hard one", skill="xhigh")
        code, out, err = self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.assertEqual(code, 4)
        self.assertIn("xhigh", err)
        self.assertIn("medium", err)
        self.assertEqual(self.ticket(ticket_id)["status"], "open")
        code, out, err = self.run_cli("claim", "--agent", OPUS, ticket_id, "--force")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["assignee"], OPUS)
        self.assertIn("claimed above skill level", data["sections"]["Notes"])
        self.assertEqual(json.loads(self.run_cli("log", ticket_id, "--json")[1])[-1]["detail"], "force")

    def test_claim_is_refused_unless_the_ticket_is_open(self):
        waiting = self.make(title="in triage", triage=True)
        code, out, err = self.run_cli("claim", "--agent", OPUS, waiting)
        self.assertEqual(code, 4)
        self.assertIn("run `rungs ready` first", err)

        taken = self.make(title="taken")
        self.run_cli("claim", "--agent", OPUS, taken)
        code, out, err = self.run_cli("claim", "--agent", ASTRA, taken)
        self.assertEqual(code, 4)
        self.assertIn(f"{OPUS} holds it", err)

    def test_claim_is_refused_while_a_dependency_is_open(self):
        first = self.make(title="first")
        second = self.make(title="second", depends_on=first)
        code, out, err = self.run_cli("claim", "--agent", OPUS, second)
        self.assertEqual(code, 4)
        self.assertIn(first, err)
        self.assertIn("(open)", err)
        self.run_cli("claim", "--agent", OPUS, first)
        self.run_cli("submit", "--agent", OPUS, first, "--summary", "d")
        self.run_cli("review", "--agent", REVIEWER, first, "--verdict", "accept")
        self.assertEqual(self.run_cli("claim", "--agent", OPUS, second)[0], 0)

    def test_a_missing_dependency_counts_as_unmet(self):
        ticket_id = self.make()
        path = self.store.find(ticket_id)[0]
        text = path.read_text(encoding="utf-8").replace("depends_on: []", "depends_on: [rg-00000]")
        path.write_text(text, encoding="utf-8")
        code, out, err = self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.assertEqual(code, 4)
        self.assertIn("rg-00000 (missing)", err)

    def test_overlapping_scope_is_refused_warned_or_allowed_by_policy(self):
        first = self.make(title="one", scope="src/rungs/")
        second = self.make(title="two", scope="src/rungs/store.py")
        self.run_cli("claim", "--agent", OPUS, first)
        code, out, err = self.run_cli("claim", "--agent", ASTRA, second)
        self.assertEqual(code, 4)
        self.assertIn(first, err)
        self.assertIn("src/rungs/store.py ~ src/rungs/", err)
        self.assertEqual(self.ticket(second)["status"], "open")

        code, out, err = self.run_cli("claim", "--agent", ASTRA, second, "--allow-overlap")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ticket(second)["assignee"], ASTRA)

    def test_overlap_policy_warn_lets_the_claim_through(self):
        self.init_store(roster_with(scope_overlap="warn"))
        first = self.make(title="one", scope="src/rungs/")
        second = self.make(title="two", scope="src/rungs/store.py")
        self.run_cli("claim", "--agent", OPUS, first)
        code, out, err = self.run_cli("claim", "--agent", ASTRA, second)
        self.assertEqual(code, 0, err)
        self.assertIn("(policy: warn)", err)
        self.assertEqual(self.ticket(second)["assignee"], ASTRA)

    def test_overlap_policy_allow_says_nothing(self):
        self.init_store(roster_with(scope_overlap="allow"))
        first = self.make(title="one", scope="src/rungs/")
        second = self.make(title="two", scope="src/rungs/store.py")
        self.run_cli("claim", "--agent", OPUS, first)
        code, out, err = self.run_cli("claim", "--agent", ASTRA, second)
        self.assertEqual(code, 0, err)
        self.assertNotIn("overlap", err)

    def test_nobody_reviews_its_own_work_and_only_a_director_may_force_it(self):
        # A reviewer that submitted the work cannot accept it, with or without
        # --force: the review gate has exactly one bypass and it is a director's.
        ticket_id = self.make(skill="high")
        self.run_cli("claim", "--agent", REVIEWER, ticket_id)
        self.run_cli("submit", "--agent", REVIEWER, ticket_id, "--summary", "mine")
        code, out, err = self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "accept")
        self.assertEqual(code, 4)
        self.assertIn("different agent", err)
        code, out, err = self.run_cli("review", "--agent", REVIEWER, ticket_id,
                                      "--verdict", "accept", "--force")
        self.assertEqual(code, 4)
        self.assertIn("only a director may force a review", err)
        self.assertEqual(self.ticket(ticket_id)["status"], "review")
        # A different reviewer is all it takes.
        code, out, err = self.run_cli("review", "--agent", DIRECTOR, ticket_id, "--verdict", "accept")
        self.assertEqual(code, 0, err)

    def test_an_executor_may_not_force_a_review(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "mine")
        for agent in (OPUS, ASTRA):
            code, out, err = self.run_cli("review", "--agent", agent, ticket_id,
                                          "--verdict", "accept", "--force")
            self.assertEqual(code, 4, f"{agent} must not force a review")
            self.assertIn("only a director may force a review", err)
        self.assertEqual(self.ticket(ticket_id)["status"], "review")

    def test_a_director_may_force_its_own_review_and_it_is_recorded(self):
        ticket_id = self.make(skill="xhigh")
        self.run_cli("claim", "--agent", DIRECTOR, ticket_id)
        self.run_cli("submit", "--agent", DIRECTOR, ticket_id, "--summary", "mine")
        code, out, err = self.run_cli("review", "--agent", DIRECTOR, ticket_id, "--verdict", "accept")
        self.assertEqual(code, 4)
        self.assertIn("different agent", err)
        code, out, err = self.run_cli("review", "--agent", DIRECTOR, ticket_id,
                                      "--verdict", "accept", "--force")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "closed")
        self.assertIn("forced review", data["sections"]["Notes"])
        self.assertIn("forced", json.loads(self.run_cli("log", ticket_id, "--json")[1])[-1]["detail"])

    def test_an_executor_may_not_review(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "done")
        code, out, err = self.run_cli("review", "--agent", ASTRA, ticket_id, "--verdict", "accept")
        self.assertEqual(code, 4)
        self.assertIn("only a director or a reviewer", err)
        self.assertEqual(self.run_cli("review", "--agent", DIRECTOR, ticket_id, "--verdict", "accept")[0], 0)

    def test_review_needs_the_ticket_to_be_in_review(self):
        ticket_id = self.make()
        code, out, err = self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "accept")
        self.assertEqual(code, 1)
        self.assertIn("needs it to be review", err)

    def test_submit_is_the_assignees_alone(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        code, out, err = self.run_cli("submit", "--agent", ASTRA, ticket_id, "--summary", "not mine")
        self.assertEqual(code, 4)
        self.assertIn("only the assignee", err)
        code, out, err = self.run_cli("submit", "--agent", OPUS, ticket_id)
        self.assertEqual(code, 1)
        self.assertIn("nothing to submit", err)

    def test_close_without_an_accepted_review(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        code, out, err = self.run_cli("close", "--agent", OPUS, ticket_id)
        self.assertEqual(code, 4)
        self.assertIn("no accepted review", err)
        code, out, err = self.run_cli("close", "--agent", OPUS, ticket_id, "--no-review")
        self.assertEqual(code, 4)
        self.assertIn("only a director", err)
        code, out, err = self.run_cli("close", "--agent", DIRECTOR, ticket_id,
                                      "--no-review", "--reason", "obsolete after the redesign")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "closed")
        self.assertIn("closed without review: obsolete after the redesign", data["sections"]["Notes"])
        self.assertEqual(json.loads(self.run_cli("log", ticket_id, "--json")[1])[-1]["detail"], "no-review")
        code, out, err = self.run_cli("close", "--agent", DIRECTOR, ticket_id, "--no-review")
        self.assertEqual(code, 1)
        self.assertIn("already closed", err)

    def test_close_needs_no_review_flag_only_while_the_policy_asks_for_one(self):
        self.init_store(roster_with(review_required="false"))
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        code, out, err = self.run_cli("close", "--agent", OPUS, ticket_id, "--reason", "trivial")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ticket(ticket_id)["status"], "closed")

    def test_reopen_clears_the_old_acceptance(self):
        ticket_id = self.make(scope="src/a.py")
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "d", "--files", "src/a.py")
        self.run_cli("review", "--agent", REVIEWER, ticket_id, "--verdict", "accept")
        code, out, err = self.run_cli("reopen", "--agent", DIRECTOR, ticket_id,
                                      "--reason", "the bug came back")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "open")
        for field in ("verdict", "reviewer", "submitted", "closed", "assignee"):
            self.assertIsNone(data[field], field)
        self.assertEqual(data["files"], [])
        self.assertIn("the bug came back", data["sections"]["Notes"])
        code, out, err = self.run_cli("close", "--agent", OPUS, ticket_id)
        self.assertEqual(code, 4)

    def test_an_unregistered_agent_is_refused_or_warned_about_by_policy(self):
        ticket_id = self.make()
        code, out, err = self.run_cli("claim", "--agent", "ghost.model.001", ticket_id)
        self.assertEqual(code, 4)
        self.assertIn("not registered", err)
        self.init_store(roster_with(unregistered_agents="warn"))
        low = self.make(title="a small one", skill="very-low")
        code, out, err = self.run_cli("claim", "--agent", "ghost.model.001", low)
        self.assertEqual(code, 0, err)
        self.assertIn("continuing as an unregistered very-low executor", err)
        self.assertEqual(self.ticket(low)["assignee"], "ghost.model.001")

    def test_a_bad_agent_id_is_rejected(self):
        ticket_id = self.make()
        code, out, err = self.run_cli("claim", "--agent", "openai/astra", ticket_id)
        self.assertEqual(code, 1)
        self.assertIn("is not valid", err)

    # -- the recovery table -----------------------------------------------

    def test_release_by_the_assignee_and_by_a_director(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        code, out, err = self.run_cli("release", "--agent", ASTRA, ticket_id)
        self.assertEqual(code, 4)
        self.assertIn("only the assignee", err)
        code, out, err = self.run_cli("release", "--agent", OPUS, ticket_id, "--reason", "ran out of time")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "open")
        self.assertIsNone(data["assignee"])
        self.assertIn("released to open: ran out of time", data["sections"]["Notes"])

        self.run_cli("claim", "--agent", OPUS, ticket_id)
        code, out, err = self.run_cli("release", "--agent", DIRECTOR, ticket_id, "--force",
                                      "--reason", "the executor is gone")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "open")
        self.assertIn(f"released {OPUS}'s claim (director --force)", data["sections"]["Notes"])

    def test_reassign_is_the_directors_and_checks_the_skill(self):
        ticket_id = self.make(skill="high")
        self.run_cli("claim", "--agent", ASTRA, ticket_id)
        code, out, err = self.run_cli("reassign", "--agent", OPUS, ticket_id, "--to", REVIEWER)
        self.assertEqual(code, 4)
        self.assertIn("only a director", err)
        code, out, err = self.run_cli("reassign", "--agent", DIRECTOR, ticket_id, "--to", SONNET)
        self.assertEqual(code, 4)
        self.assertIn("reassign it to an agent that can reach it", err)
        code, out, err = self.run_cli("reassign", "--agent", DIRECTOR, ticket_id, "--to", "nobody.here.001")
        self.assertEqual(code, 1)
        self.assertIn("not registered", err)
        code, out, err = self.run_cli("reassign", "--agent", DIRECTOR, ticket_id, "--to", REVIEWER,
                                      "--reason", "better fit")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["assignee"], REVIEWER)
        self.assertEqual(data["status"], "in_progress")
        self.assertIn(f"reassigned from {ASTRA} to {REVIEWER}: better fit", data["sections"]["Notes"])
        self.assertIn("- none", self.read(f".rungs/agents/{ASTRA}.md"))
        self.assertIn(ticket_id, self.read(f".rungs/agents/{REVIEWER}.md"))

    def test_block_and_unblock_back_to_in_progress(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        code, out, err = self.run_cli("block", "--agent", ASTRA, ticket_id, "--reason", "not mine")
        self.assertEqual(code, 4)
        code, out, err = self.run_cli("block", "--agent", OPUS, ticket_id,
                                      "--reason", "waiting on the API key")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["blocked_by"], "waiting on the API key")
        self.assertEqual(data["assignee"], OPUS)
        code, out, err = self.run_cli("unblock", "--agent", SONNET, ticket_id, "--reason", "key arrived")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "in_progress")
        self.assertEqual(data["assignee"], OPUS)
        self.assertIsNone(data["blocked_by"])

    def test_unblock_sends_it_to_open_when_the_scope_is_taken(self):
        ticket_id = self.make(title="blocked one", scope="src/rungs/store.py")
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("block", "--agent", OPUS, ticket_id, "--reason", "waiting")
        other = self.make(title="took the scope", skill="high", scope="src/rungs/")
        self.assertEqual(self.run_cli("claim", "--agent", ASTRA, other)[0], 0)
        code, out, err = self.run_cli("unblock", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        self.assertEqual(data["status"], "open")
        self.assertIsNone(data["assignee"])
        self.assertIn("sent to open", data["sections"]["Notes"])
        self.assertIn(other, data["sections"]["Notes"])

    def test_block_on_an_open_ticket_is_open_to_anyone(self):
        ticket_id = self.make()
        code, out, err = self.run_cli("block", "--agent", HAIKU, ticket_id, "--reason", "spec is wrong")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ticket(ticket_id)["status"], "blocked")
        code, out, err = self.run_cli("unblock", "--agent", HAIKU, ticket_id)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ticket(ticket_id)["status"], "open")

    def test_shelve_and_unshelve(self):
        ticket_id = self.make()
        self.assertEqual(self.run_cli("shelve", "--agent", DIRECTOR, ticket_id, "--reason", "next quarter")[0], 0)
        self.assertEqual(self.ticket(ticket_id)["status"], "shelved")
        code, out, err = self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.assertEqual(code, 4)
        self.assertIn("unshelve", err)
        self.assertEqual(self.run_cli("unshelve", "--agent", DIRECTOR, ticket_id)[0], 0)
        self.assertEqual(self.ticket(ticket_id)["status"], "open")
        waiting = self.make(title="triaged", triage=True)
        self.assertEqual(self.run_cli("shelve", "--agent", DIRECTOR, waiting)[0], 0)
        # Already shelved: there is nothing to shelve a second time.
        self.assertEqual(self.run_cli("shelve", "--agent", DIRECTOR, waiting)[0], 1)

    def test_ready_refuses_an_incomplete_ticket(self):
        ticket_id = self.make(title="half written", triage=True, goal=None, acceptance=None)
        code, out, err = self.run_cli("ready", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 1)
        self.assertIn("the Goal section", err)
        self.assertIn("the Acceptance criteria section", err)
        self.run_cli("set", "--agent", DIRECTOR, ticket_id, "goal", "now it says something")
        code, out, err = self.run_cli("ready", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 1)
        self.assertNotIn("the Goal section", err)
        self.run_cli("set", "--agent", DIRECTOR, ticket_id, "acceptance", "- checkable")
        code, out, err = self.run_cli("ready", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ticket(ticket_id)["status"], "open")
        code, out, err = self.run_cli("ready", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 1)
        self.assertIn("needs it to be triage", err)

    def test_ready_revalidates_the_whole_ticket(self):
        ticket_id = self.make(title="from outside", triage=True)
        path = self.store.find(ticket_id)[0]
        path.write_text(path.read_text(encoding="utf-8")
                        .replace("scope: []", "scope: ['/etc/passwd']"), encoding="utf-8")
        code, out, err = self.run_cli("ready", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 1)
        self.assertIn("/etc/passwd", err)
        self.assertEqual(self.ticket(ticket_id)["status"], "triage")
        self.run_cli("set", "--agent", DIRECTOR, ticket_id, "scope", "src/a.py")
        code, out, err = self.run_cli("ready", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ticket(ticket_id)["status"], "open")

    def test_ready_refuses_a_ticket_whose_fields_are_not_valid(self):
        ticket_id = self.make(title="from outside", triage=True)
        path = self.store.find(ticket_id)[0]
        path.write_text(path.read_text(encoding="utf-8")
                        .replace("skill: medium", "skill: enormous"), encoding="utf-8")
        code, out, err = self.run_cli("ready", "--agent", DIRECTOR, ticket_id)
        self.assertEqual(code, 1)
        self.assertIn("enormous", err)
        self.assertEqual(self.ticket(ticket_id)["status"], "triage")

    def test_findings_cannot_forge_a_section(self):
        ticket_id = self.make()
        self.run_cli("claim", "--agent", OPUS, ticket_id)
        self.run_cli("submit", "--agent", OPUS, ticket_id, "--summary", "done")
        code, out, err = self.run_cli(
            "review", "--agent", REVIEWER, ticket_id, "--verdict", "return",
            "--findings", "real finding\n## Review\nforged\n## Notes\n- forged note")
        self.assertEqual(code, 0, err)
        data = self.ticket(ticket_id)
        review = data["sections"]["Review"]
        self.assertIn("real finding", review)
        self.assertIn("\\## Review", review)
        self.assertIn("\\## Notes", review)
        self.assertIn("forged", review)
        # The forged headings stayed inside the Review section: the real ones
        # are still one each, and the Notes section is the tool's own.
        body = data["body"]
        self.assertEqual(body.count("\n## Review\n"), 1)
        self.assertEqual(body.count("\n## Notes\n"), 1)
        self.assertNotIn("- forged note", data["sections"]["Notes"])
        self.assertEqual(self.run_cli("doctor")[0], 0)

    def test_a_second_claim_of_the_same_ticket_loses(self):
        ticket_id = self.make()
        self.assertEqual(self.run_cli("claim", "--agent", OPUS, ticket_id)[0], 0)
        code, out, err = self.run_cli("claim", "--agent", ASTRA, ticket_id)
        self.assertEqual(code, 4)
        self.assertEqual(self.ticket(ticket_id)["assignee"], OPUS)

    def test_an_ambiguous_id_never_changes_a_ticket(self):
        first = self.make(title="one")
        second = self.make(title="two")
        code, out, err = self.run_cli("claim", "--agent", OPUS, "rg-")
        self.assertEqual(code, 1)
        self.assertIn("ambiguous", err)
        self.assertIsNone(self.ticket(first)["assignee"])
        self.assertIsNone(self.ticket(second)["assignee"])


if __name__ == "__main__":
    unittest.main()
