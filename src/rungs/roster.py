"""The roster: rungs.yaml, agents with their skill level and role, policy.

The roster is the document that tells an agent its own level. Nothing in the
tool self-assesses: an id that is not in the roster is refused (or warned
about) on every state-changing command, per policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import miniyaml
from .ticket import AGENT_ID_PATTERN, KINDS, SKILLS, TicketError, normalise_skill, skill_rank

__all__ = [
    "ROLES", "CONFIG_FILENAMES", "RosterError", "UnknownAgent", "Agent", "Policy",
    "IntakeSource", "IntakeConfig", "Roster", "parse_agent_spec", "template",
]

ROLES = ["director", "executor", "reviewer", "critic"]
CONFIG_FILENAMES = ["rungs.yaml", ".rungs.yaml"]
OVERLAP_POLICIES = ["refuse", "warn", "allow"]
UNREGISTERED_POLICIES = ["refuse", "warn"]


class RosterError(Exception):
    pass


class UnknownAgent(RosterError):
    pass


def validate_agent_id(agent_id: str) -> str:
    agent_id = str(agent_id).strip()
    if not AGENT_ID_PATTERN.match(agent_id):
        raise RosterError(
            f"agent id {agent_id!r} is not valid: use company.model.instance with "
            "lower-case letters, digits, dots and dashes, e.g. claude.opus.001"
        )
    return agent_id


@dataclass
class Agent:
    id: str
    skill: str
    role: str = "executor"
    floor: Optional[str] = None

    @property
    def rank(self) -> int:
        return skill_rank(self.skill)

    @property
    def floor_rank(self) -> int:
        return skill_rank(self.floor) if self.floor else 1

    @property
    def is_director(self) -> bool:
        return self.role == "director"

    @property
    def can_review(self) -> bool:
        return self.role in ("director", "reviewer")

    def to_dict(self) -> dict:
        return {"id": self.id, "skill": self.skill, "rank": self.rank, "role": self.role, "floor": self.floor}


@dataclass
class Policy:
    review_required: bool = True
    scope_overlap: str = "refuse"
    unregistered_agents: str = "refuse"
    stale_after_hours: int = 24

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class IntakeSource:
    name: str
    epic: Optional[str] = None
    domain: Optional[str] = None
    kind: Optional[str] = None
    skill: Optional[str] = None
    priority: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    ready: bool = False


@dataclass
class IntakeConfig:
    default_skill: str = "medium"
    default_kind: str = "fix"
    default_priority: Optional[int] = 15
    allow_unknown_sources: bool = False
    sources: Dict[str, IntakeSource] = field(default_factory=dict)


@dataclass
class Roster:
    project: Optional[str] = None
    agents: Dict[str, Agent] = field(default_factory=dict)
    policy: Policy = field(default_factory=Policy)
    intake: IntakeConfig = field(default_factory=IntakeConfig)
    path: Optional[Path] = None
    raw: dict = field(default_factory=dict)

    # -- loading ----------------------------------------------------------

    @classmethod
    def find_path(cls, project_root: Path) -> Optional[Path]:
        for name in CONFIG_FILENAMES:
            candidate = project_root / name
            if candidate.is_file():
                return candidate
        return None

    @classmethod
    def load(cls, project_root: Path) -> "Roster":
        path = cls.find_path(project_root)
        if path is None:
            return cls()
        try:
            data = miniyaml.loads(path.read_text(encoding="utf-8"))
        except (miniyaml.YamlError, OSError, UnicodeDecodeError) as exc:
            raise RosterError(f"{path}: {exc}")
        return cls.from_dict(data, path)

    @classmethod
    def from_dict(cls, data: dict, path: Optional[Path] = None) -> "Roster":
        roster = cls(path=path, raw=data)
        project = data.get("project")
        roster.project = str(project) if project is not None else None
        agents = data.get("agents") or {}
        if not isinstance(agents, dict):
            raise RosterError("rungs.yaml: 'agents' must be a mapping of agent id to settings")
        for agent_id, settings in agents.items():
            agent_id = validate_agent_id(agent_id)
            if not isinstance(settings, dict):
                raise RosterError(f"rungs.yaml: agent {agent_id} must be a mapping with a 'skill'")
            if settings.get("skill") is None:
                raise RosterError(f"rungs.yaml: agent {agent_id} has no 'skill'")
            try:
                skill = normalise_skill(settings["skill"])
                floor = normalise_skill(settings["floor"]) if settings.get("floor") is not None else None
            except TicketError as exc:
                raise RosterError(f"rungs.yaml: agent {agent_id}: {exc}")
            role = str(settings.get("role") or "executor")
            if role not in ROLES:
                raise RosterError(f"rungs.yaml: agent {agent_id}: role {role!r} is not one of {', '.join(ROLES)}")
            if floor and skill_rank(floor) > skill_rank(skill):
                raise RosterError(f"rungs.yaml: agent {agent_id}: floor {floor} is above its skill {skill}")
            unknown = sorted(k for k in settings if k not in ("skill", "role", "floor"))
            if unknown:
                raise RosterError(f"rungs.yaml: agent {agent_id}: unknown settings {', '.join(unknown)}")
            roster.agents[agent_id] = Agent(id=agent_id, skill=skill, role=role, floor=floor)

        policy = data.get("policy") or {}
        if not isinstance(policy, dict):
            raise RosterError("rungs.yaml: 'policy' must be a mapping")
        roster.policy = Policy()
        for key, value in policy.items():
            if key == "review_required":
                roster.policy.review_required = bool(value)
            elif key == "scope_overlap":
                if value not in OVERLAP_POLICIES:
                    raise RosterError(f"rungs.yaml: policy.scope_overlap must be one of {', '.join(OVERLAP_POLICIES)}")
                roster.policy.scope_overlap = value
            elif key == "unregistered_agents":
                if value not in UNREGISTERED_POLICIES:
                    raise RosterError(f"rungs.yaml: policy.unregistered_agents must be one of {', '.join(UNREGISTERED_POLICIES)}")
                roster.policy.unregistered_agents = value
            elif key == "stale_after_hours":
                if not isinstance(value, int) or value <= 0:
                    raise RosterError("rungs.yaml: policy.stale_after_hours must be a positive integer")
                roster.policy.stale_after_hours = value
            else:
                raise RosterError(f"rungs.yaml: unknown policy setting {key!r}")

        intake = data.get("intake") or {}
        if not isinstance(intake, dict):
            raise RosterError("rungs.yaml: 'intake' must be a mapping")
        roster.intake = IntakeConfig()
        for key, value in intake.items():
            if key == "default_skill":
                roster.intake.default_skill = _skill_or_error(value, "intake.default_skill")
            elif key == "default_kind":
                roster.intake.default_kind = _kind_or_error(value, "intake.default_kind")
            elif key == "default_priority":
                roster.intake.default_priority = _int_or_none(value, "intake.default_priority")
            elif key == "allow_unknown_sources":
                roster.intake.allow_unknown_sources = bool(value)
            elif key == "sources":
                if not isinstance(value, dict):
                    raise RosterError("rungs.yaml: intake.sources must be a mapping")
                for name, block in value.items():
                    if not re.match(r"^[a-z0-9-]{1,40}$", str(name)):
                        raise RosterError(f"rungs.yaml: intake source name {name!r} must match [a-z0-9-]")
                    block = block or {}
                    if not isinstance(block, dict):
                        raise RosterError(f"rungs.yaml: intake source {name} must be a mapping")
                    source = IntakeSource(name=str(name))
                    for skey, sval in block.items():
                        if skey == "epic":
                            source.epic = _str_or_none(sval)
                        elif skey == "domain":
                            source.domain = _str_or_none(sval)
                        elif skey == "kind":
                            source.kind = _kind_or_error(sval, f"intake.sources.{name}.kind") if sval is not None else None
                        elif skey == "skill":
                            source.skill = _skill_or_error(sval, f"intake.sources.{name}.skill") if sval is not None else None
                        elif skey == "priority":
                            source.priority = _int_or_none(sval, f"intake.sources.{name}.priority")
                        elif skey == "tags":
                            if sval is None:
                                source.tags = []
                            elif isinstance(sval, list):
                                source.tags = [str(t) for t in sval]
                            else:
                                raise RosterError(f"rungs.yaml: intake.sources.{name}.tags must be a list")
                        elif skey == "ready":
                            source.ready = bool(sval)
                        else:
                            raise RosterError(f"rungs.yaml: intake.sources.{name}: unknown setting {skey!r}")
                    roster.intake.sources[source.name] = source
            else:
                raise RosterError(f"rungs.yaml: unknown intake setting {key!r}")
        # Arbite-format files in the inbox need no configuration: an existing
        # exporter can be pointed at .rungs/inbox and its tickets land in triage.
        roster.intake.sources.setdefault("arbite", IntakeSource(name="arbite", tags=["arbite"]))
        return roster

    # -- queries ----------------------------------------------------------

    def agent(self, agent_id: str) -> Agent:
        agent_id = validate_agent_id(agent_id)
        try:
            return self.agents[agent_id]
        except KeyError:
            where = f" in {self.path}" if self.path else " (no rungs.yaml found)"
            raise UnknownAgent(f"agent {agent_id!r} is not registered{where}")

    def resolve(self, agent_id: str) -> Tuple[Optional[Agent], Optional[str]]:
        """(agent, warning). Unknown ids raise under policy 'refuse' and are
        returned as None with a warning under 'warn'."""
        try:
            return self.agent(agent_id), None
        except UnknownAgent as exc:
            if self.policy.unregistered_agents == "warn":
                return None, str(exc)
            raise

    def directors(self) -> List[Agent]:
        return [a for a in self.agents.values() if a.is_director]

    def reviewers(self) -> List[Agent]:
        return [a for a in self.agents.values() if a.can_review]

    def by_skill(self) -> List[Agent]:
        return sorted(self.agents.values(), key=lambda a: (-a.rank, a.id))

    # -- writing ----------------------------------------------------------

    def to_dict(self) -> dict:
        data: dict = {}
        if self.project is not None:
            data["project"] = self.project
        agents: dict = {}
        for agent in self.agents.values():
            entry: dict = {"skill": agent.skill, "role": agent.role}
            if agent.floor:
                entry["floor"] = agent.floor
            agents[agent.id] = entry
        data["agents"] = agents
        data["policy"] = self.policy.to_dict()
        intake: dict = {
            "default_skill": self.intake.default_skill,
            "default_kind": self.intake.default_kind,
            "default_priority": self.intake.default_priority,
            "allow_unknown_sources": self.intake.allow_unknown_sources,
        }
        if self.intake.sources:
            sources: dict = {}
            for source in self.intake.sources.values():
                block: dict = {}
                for key in ("epic", "domain", "kind", "skill", "priority"):
                    value = getattr(source, key)
                    if value is not None:
                        block[key] = value
                if source.tags:
                    block["tags"] = list(source.tags)
                block["ready"] = source.ready
                sources[source.name] = block
            intake["sources"] = sources
        data["intake"] = intake
        return data

    def dumps(self) -> str:
        header = (
            "# rungs roster and policy. Agent ids are company.model.instance. Every\n"
            "# agent has a skill level (very-low | low | medium | high | xhigh) and a\n"
            "# role (director | executor | reviewer | critic). See .rungs/AGENTS.md.\n"
        )
        return header + miniyaml.dumps(self.to_dict())

    def add_agent(self, agent: Agent) -> bool:
        """Add or update an agent. Returns True when something changed."""
        current = self.agents.get(agent.id)
        if current == agent:
            return False
        self.agents[agent.id] = agent
        return True


def _skill_or_error(value, where: str) -> str:
    try:
        return normalise_skill(value)
    except TicketError as exc:
        raise RosterError(f"rungs.yaml: {where}: {exc}")


def _kind_or_error(value, where: str) -> str:
    value = str(value)
    if value not in KINDS:
        raise RosterError(f"rungs.yaml: {where}: kind {value!r} is not one of {', '.join(KINDS)}")
    return value


def _int_or_none(value, where: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int):
        raise RosterError(f"rungs.yaml: {where} must be an integer")
    return value


def _str_or_none(value) -> Optional[str]:
    return None if value is None else str(value)


def parse_agent_spec(spec: str) -> Agent:
    """'id:skill[:role[:floor]]' as given to init/deploy --agent."""
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) < 2 or len(parts) > 4 or not parts[0] or not parts[1]:
        raise RosterError(f"agent spec {spec!r} must be id:skill[:role[:floor]]")
    agent_id = validate_agent_id(parts[0])
    try:
        skill = normalise_skill(parts[1])
        floor = normalise_skill(parts[3]) if len(parts) == 4 and parts[3] else None
    except TicketError as exc:
        raise RosterError(f"agent spec {spec!r}: {exc}")
    role = parts[2] if len(parts) >= 3 and parts[2] else "executor"
    if role not in ROLES:
        raise RosterError(f"agent spec {spec!r}: role must be one of {', '.join(ROLES)}")
    return Agent(id=agent_id, skill=skill, role=role, floor=floor)


def template(project: Optional[str], agents: List[Agent]) -> str:
    """The rungs.yaml written by init: the given agents, or commented examples."""
    roster = Roster(project=project or None)
    for agent in agents:
        roster.add_agent(agent)
    text = roster.dumps()
    if not agents:
        text = text.replace(
            "agents: {}\n",
            "agents:\n"
            "  # Add one entry per agent before any work can be claimed, for example:\n"
            "  # claude.fable.001:\n"
            "  #   skill: xhigh\n"
            "  #   role: director\n"
            "  # claude.opus.001:\n"
            "  #   skill: medium\n"
            "  #   role: executor\n"
            "  # claude.opus.reviewer:\n"
            "  #   skill: high\n"
            "  #   role: reviewer\n",
        )
    return text
