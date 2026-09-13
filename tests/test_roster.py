"""Tests for rungs.roster: rungs.yaml model, agent lookup, policy, skill
ranks. See DESIGN.md section 4."""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (inserts src/ onto sys.path)

from rungs import roster as R


class FromDictAgentTests(unittest.TestCase):
    def test_minimal_agent(self):
        ro = R.Roster.from_dict({"agents": {"a.b.c": {"skill": "medium"}}})
        agent = ro.agents["a.b.c"]
        self.assertEqual(agent.skill, "medium")
        self.assertEqual(agent.role, "executor")
        self.assertIsNone(agent.floor)

    def test_all_settings(self):
        ro = R.Roster.from_dict({
            "agents": {"a.b.c": {"skill": "xhigh", "role": "director", "floor": "high"}}
        })
        agent = ro.agents["a.b.c"]
        self.assertEqual(agent.skill, "xhigh")
        self.assertEqual(agent.role, "director")
        self.assertEqual(agent.floor, "high")

    def test_skill_alias_normalised(self):
        ro = R.Roster.from_dict({"agents": {"a.b.c": {"skill": "vhigh"}}})
        self.assertEqual(ro.agents["a.b.c"].skill, "xhigh")

    def test_floor_alias_normalised(self):
        ro = R.Roster.from_dict({"agents": {"a.b.c": {"skill": "xhigh", "floor": "med"}}})
        self.assertEqual(ro.agents["a.b.c"].floor, "medium")

    def test_floor_above_skill_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"a.b.c": {"skill": "low", "floor": "high"}}})

    def test_floor_equal_to_skill_ok(self):
        ro = R.Roster.from_dict({"agents": {"a.b.c": {"skill": "medium", "floor": "medium"}}})
        self.assertEqual(ro.agents["a.b.c"].floor, "medium")

    def test_unknown_setting_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"a.b.c": {"skill": "low", "bogus": 1}}})

    def test_missing_skill_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"a.b.c": {"role": "executor"}}})

    def test_unknown_skill_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"a.b.c": {"skill": "super-duper"}}})

    def test_unknown_role_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"a.b.c": {"skill": "low", "role": "wizard"}}})

    def test_bad_agent_id_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"not_dotted": {"skill": "low"}}})

    def test_bad_agent_id_with_slash_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"claude/opus/001": {"skill": "low"}}})

    def test_agents_not_a_mapping_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": ["a.b.c"]})

    def test_agent_settings_not_a_mapping_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"agents": {"a.b.c": "medium"}})


class PolicyTests(unittest.TestCase):
    def test_defaults(self):
        ro = R.Roster.from_dict({})
        self.assertTrue(ro.policy.review_required)
        self.assertEqual(ro.policy.scope_overlap, "refuse")
        self.assertEqual(ro.policy.unregistered_agents, "refuse")
        self.assertEqual(ro.policy.stale_after_hours, 24)

    def test_scope_overlap_values(self):
        for value in ("refuse", "warn", "allow"):
            ro = R.Roster.from_dict({"policy": {"scope_overlap": value}})
            self.assertEqual(ro.policy.scope_overlap, value)

    def test_scope_overlap_bad_value(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"policy": {"scope_overlap": "sometimes"}})

    def test_unregistered_agents_values(self):
        for value in ("refuse", "warn"):
            ro = R.Roster.from_dict({"policy": {"unregistered_agents": value}})
            self.assertEqual(ro.policy.unregistered_agents, value)

    def test_unregistered_agents_bad_value(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"policy": {"unregistered_agents": "maybe"}})

    def test_stale_after_hours_must_be_positive_int(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"policy": {"stale_after_hours": 0}})
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"policy": {"stale_after_hours": "many"}})

    def test_unknown_policy_setting_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"policy": {"bogus": True}})

    def test_review_required_coerced_to_bool(self):
        ro = R.Roster.from_dict({"policy": {"review_required": 0}})
        self.assertFalse(ro.policy.review_required)


class IntakeConfigTests(unittest.TestCase):
    def test_defaults(self):
        ro = R.Roster.from_dict({})
        self.assertEqual(ro.intake.default_skill, "medium")
        self.assertEqual(ro.intake.default_kind, "fix")
        self.assertEqual(ro.intake.default_priority, 15)
        self.assertFalse(ro.intake.allow_unknown_sources)
        # The built-in arbite source is always present so an old exporter needs no configuration.
        self.assertEqual(list(ro.intake.sources), ["arbite"])
        self.assertEqual(ro.intake.sources["arbite"].tags, ["arbite"])

    def test_source_all_fields(self):
        ro = R.Roster.from_dict({
            "intake": {
                "sources": {
                    "helpdesk": {
                        "epic": "user-requests", "domain": "ui", "kind": "fix",
                        "skill": "medium", "priority": 5, "tags": ["a", "b"], "ready": True,
                    }
                }
            }
        })
        src = ro.intake.sources["helpdesk"]
        self.assertEqual(src.epic, "user-requests")
        self.assertEqual(src.domain, "ui")
        self.assertEqual(src.kind, "fix")
        self.assertEqual(src.skill, "medium")
        self.assertEqual(src.priority, 5)
        self.assertEqual(src.tags, ["a", "b"])
        self.assertTrue(src.ready)

    def test_source_default_ready_false(self):
        ro = R.Roster.from_dict({"intake": {"sources": {"s1": {}}}})
        self.assertFalse(ro.intake.sources["s1"].ready)

    def test_source_bad_name_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"intake": {"sources": {"Bad Name!": {}}}})

    def test_source_unknown_field_rejected(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"intake": {"sources": {"s1": {"bogus": 1}}}})

    def test_default_skill_alias(self):
        ro = R.Roster.from_dict({"intake": {"default_skill": "vhigh"}})
        self.assertEqual(ro.intake.default_skill, "xhigh")

    def test_default_kind_bad_value(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"intake": {"default_kind": "not-a-kind"}})

    def test_default_priority_must_be_int(self):
        with self.assertRaises(R.RosterError):
            R.Roster.from_dict({"intake": {"default_priority": "high"}})

    def test_allow_unknown_sources_bool(self):
        ro = R.Roster.from_dict({"intake": {"allow_unknown_sources": True}})
        self.assertTrue(ro.intake.allow_unknown_sources)


class AgentAndResolveTests(unittest.TestCase):
    def _roster(self, unregistered="refuse"):
        return R.Roster.from_dict({
            "agents": {"claude.opus.001": {"skill": "medium", "role": "executor"}},
            "policy": {"unregistered_agents": unregistered},
        })

    def test_agent_lookup(self):
        ro = self._roster()
        agent = ro.agent("claude.opus.001")
        self.assertEqual(agent.skill, "medium")

    def test_unknown_agent_raises(self):
        ro = self._roster()
        with self.assertRaises(R.UnknownAgent):
            ro.agent("nope.body.here")

    def test_invalid_agent_id_raises_roster_error(self):
        ro = self._roster()
        with self.assertRaises(R.RosterError):
            ro.agent("bad/id")

    def test_resolve_known_agent(self):
        ro = self._roster()
        agent, warning = ro.resolve("claude.opus.001")
        self.assertIsNotNone(agent)
        self.assertIsNone(warning)

    def test_resolve_unknown_under_refuse_raises(self):
        ro = self._roster(unregistered="refuse")
        with self.assertRaises(R.UnknownAgent):
            ro.resolve("nope.body.here")

    def test_resolve_unknown_under_warn_returns_none_and_message(self):
        ro = self._roster(unregistered="warn")
        agent, warning = ro.resolve("nope.body.here")
        self.assertIsNone(agent)
        self.assertIsInstance(warning, str)
        self.assertIn("nope.body.here", warning)

    def test_agent_rank_property(self):
        agent = R.Agent(id="a.b.c", skill="high")
        self.assertEqual(agent.rank, 4)

    def test_agent_floor_rank_defaults_to_one(self):
        agent = R.Agent(id="a.b.c", skill="high")
        self.assertEqual(agent.floor_rank, 1)

    def test_agent_floor_rank_uses_floor(self):
        agent = R.Agent(id="a.b.c", skill="high", floor="medium")
        self.assertEqual(agent.floor_rank, 3)

    def test_agent_is_director(self):
        self.assertTrue(R.Agent(id="a.b.c", skill="xhigh", role="director").is_director)
        self.assertFalse(R.Agent(id="a.b.c", skill="xhigh", role="executor").is_director)

    def test_agent_can_review(self):
        self.assertTrue(R.Agent(id="a.b.c", skill="high", role="reviewer").can_review)
        self.assertTrue(R.Agent(id="a.b.c", skill="xhigh", role="director").can_review)
        self.assertFalse(R.Agent(id="a.b.c", skill="high", role="executor").can_review)


class DumpsLoadRoundTripTests(unittest.TestCase):
    def test_round_trip(self):
        ro = R.Roster(project="proj")
        ro.add_agent(R.Agent(id="a.b.c", skill="high", role="reviewer"))
        ro.add_agent(R.Agent(id="d.e.f", skill="medium", role="executor", floor="low"))
        ro.policy.scope_overlap = "warn"
        ro.intake.sources["src1"] = R.IntakeSource(
            name="src1", epic="e", domain="d", kind="fix", skill="low",
            priority=3, tags=["x", "y"], ready=True,
        )
        text = ro.dumps()
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "rungs.yaml").write_text(text, encoding="utf-8")
            loaded = R.Roster.load(project_root)
        self.assertEqual(loaded.project, "proj")
        self.assertEqual(set(loaded.agents), {"a.b.c", "d.e.f"})
        self.assertEqual(loaded.agents["d.e.f"].floor, "low")
        self.assertEqual(loaded.policy.scope_overlap, "warn")
        self.assertEqual(loaded.intake.sources["src1"].tags, ["x", "y"])
        self.assertTrue(loaded.intake.sources["src1"].ready)

    def test_load_missing_file_returns_empty_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = R.Roster.load(Path(tmp))
        self.assertEqual(loaded.agents, {})

    def test_find_path_prefers_rungs_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rungs.yaml").write_text("project: x\n", encoding="utf-8")
            self.assertEqual(R.Roster.find_path(root), root / "rungs.yaml")

    def test_find_path_falls_back_to_dotfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".rungs.yaml").write_text("project: x\n", encoding="utf-8")
            self.assertEqual(R.Roster.find_path(root), root / ".rungs.yaml")


class AddAgentTests(unittest.TestCase):
    def test_add_new_agent_returns_true(self):
        ro = R.Roster()
        self.assertTrue(ro.add_agent(R.Agent(id="a.b.c", skill="low")))

    def test_add_identical_agent_returns_false(self):
        ro = R.Roster()
        ro.add_agent(R.Agent(id="a.b.c", skill="low", role="executor"))
        self.assertFalse(ro.add_agent(R.Agent(id="a.b.c", skill="low", role="executor")))

    def test_add_changed_agent_returns_true_and_updates(self):
        ro = R.Roster()
        ro.add_agent(R.Agent(id="a.b.c", skill="low", role="executor"))
        self.assertTrue(ro.add_agent(R.Agent(id="a.b.c", skill="high", role="executor")))
        self.assertEqual(ro.agents["a.b.c"].skill, "high")


class ParseAgentSpecTests(unittest.TestCase):
    def test_id_and_skill(self):
        agent = R.parse_agent_spec("claude.opus.001:medium")
        self.assertEqual(agent.id, "claude.opus.001")
        self.assertEqual(agent.skill, "medium")
        self.assertEqual(agent.role, "executor")
        self.assertIsNone(agent.floor)

    def test_id_skill_role(self):
        agent = R.parse_agent_spec("claude.opus.001:medium:reviewer")
        self.assertEqual(agent.role, "reviewer")

    def test_id_skill_role_floor(self):
        agent = R.parse_agent_spec("claude.opus.001:medium:reviewer:low")
        self.assertEqual(agent.floor, "low")

    def test_skill_alias_in_spec(self):
        agent = R.parse_agent_spec("claude.opus.001:vhigh")
        self.assertEqual(agent.skill, "xhigh")

    def test_missing_skill_rejected(self):
        with self.assertRaises(R.RosterError):
            R.parse_agent_spec("claude.opus.001")

    def test_trailing_colon_rejected(self):
        with self.assertRaises(R.RosterError):
            R.parse_agent_spec("claude.opus.001:")

    def test_empty_id_rejected(self):
        with self.assertRaises(R.RosterError):
            R.parse_agent_spec(":medium")

    def test_bad_role_rejected(self):
        with self.assertRaises(R.RosterError):
            R.parse_agent_spec("claude.opus.001:medium:wizard")

    def test_too_many_parts_rejected(self):
        with self.assertRaises(R.RosterError):
            R.parse_agent_spec("a:b:c:d:e")

    def test_bad_agent_id_rejected(self):
        with self.assertRaises(R.RosterError):
            R.parse_agent_spec("not_dotted:medium")


class TemplateTests(unittest.TestCase):
    def test_template_with_agents_round_trips(self):
        agents = [R.Agent(id="claude.opus.001", skill="medium", role="executor")]
        text = R.template("proj", agents)
        from rungs import miniyaml as Y
        data = Y.loads(text)
        self.assertIn("claude.opus.001", data["agents"])
        ro = R.Roster.from_dict(data)
        self.assertEqual(ro.agents["claude.opus.001"].skill, "medium")

    def test_template_without_agents_parses_with_no_agents(self):
        text = R.template(None, [])
        from rungs import miniyaml as Y
        data = Y.loads(text)  # must not raise
        ro = R.Roster.from_dict(data)
        self.assertEqual(ro.agents, {})

    def test_template_without_agents_has_commented_examples(self):
        text = R.template(None, [])
        self.assertIn("# claude.fable.001:", text)


if __name__ == "__main__":
    unittest.main()
