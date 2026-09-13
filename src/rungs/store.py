"""The store: locate .rungs/, read and write tickets, order work, log events."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .roster import Agent, Roster, validate_agent_id
from .ticket import (
    FLAT_STATUS_DIRS, TMP_PREFIX, Ticket, TicketError, claim_file, create_exclusive,
    gen_id, load_ticket, move_ticket, now, save_ticket, status_dir,
)

__all__ = ["STORE_DIRNAME", "STATUS_FOLDERS", "EXTRA_FOLDERS", "StoreError", "Store", "Event"]

STORE_DIRNAME = ".rungs"
STATUS_FOLDERS = ["triage", "open", "in_progress", "review", "blocked", "shelved", "closed"]
EXTRA_FOLDERS = ["inbox", "inbox/done", "plans", "agents"]
EVENTS_FILE = "events.jsonl"


class StoreError(Exception):
    pass


@dataclass
class Event:
    at: str
    agent: Optional[str]
    action: str
    ticket: Optional[str]
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "at": self.at, "agent": self.agent, "action": self.action, "ticket": self.ticket,
            "from": self.from_status, "to": self.to_status, "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            at=str(data.get("at", "")), agent=data.get("agent"), action=str(data.get("action", "")),
            ticket=data.get("ticket"), from_status=data.get("from"), to_status=data.get("to"),
            detail=data.get("detail"),
        )


class Store:
    def __init__(self, root: Path, on_error=None):
        self.root = root.resolve()
        self.project_root = self.root.parent
        # Called with a message for every ticket file that cannot be read.
        # Reading commands skip such files instead of failing store-wide;
        # `doctor` reports them.
        self.on_error = on_error
        self._reported: set = set()

    # -- locating ---------------------------------------------------------

    @classmethod
    def locate(cls, start: Optional[Path] = None) -> Optional["Store"]:
        """Walk up from `start` (default cwd) to the nearest .rungs/ store."""
        start = (start or Path.cwd()).resolve()
        for candidate_root in [start, *start.parents]:
            candidate = candidate_root / STORE_DIRNAME
            if candidate.is_dir():
                return cls(candidate)
        return None

    @classmethod
    def require(cls, start: Optional[Path] = None) -> "Store":
        store = cls.locate(start)
        if store is None:
            raise StoreError(
                f"no {STORE_DIRNAME}/ store found in {start or Path.cwd()} or above; run 'rungs init' "
                "in the project root, or 'rungs deploy --into <project>'"
            )
        return store

    def ensure_layout(self) -> List[Path]:
        """Create every folder of the layout. Returns the folders created."""
        created: List[Path] = []
        for name in STATUS_FOLDERS + EXTRA_FOLDERS:
            folder = self.root / name
            if not folder.is_dir():
                folder.mkdir(parents=True, exist_ok=True)
                created.append(folder)
        return created

    def roster(self) -> Roster:
        return Roster.load(self.project_root)

    def agent_file(self, agent_id: str) -> Path:
        """The agent's file. The id is validated here because callers pass ids
        read from ticket files, not only from the roster."""
        return self.root / "agents" / f"{validate_agent_id(agent_id)}.md"

    # -- reading ----------------------------------------------------------

    def status_dir(self, status: str, closed_date: Optional[str] = None) -> Path:
        return status_dir(status, self.root, closed_date)

    def iter_paths(self) -> Iterator[Path]:
        for status in STATUS_FOLDERS:
            folder = self.root / status
            if not folder.is_dir():
                continue
            if status == "closed":
                for month in sorted(p for p in folder.iterdir() if p.is_dir()):
                    yield from sorted(month.glob("*.md"))
            else:
                yield from sorted(folder.glob("*.md"))

    def load_all(self) -> List[Tuple[Path, Ticket]]:
        """Every readable ticket. An unreadable file is reported once through
        `on_error` (or raised when there is no handler) and skipped, so one
        broken file never takes down every listing."""
        items, errors = self.load_all_lenient()
        for message in errors:
            if self.on_error is None:
                raise TicketError(message)
            if message not in self._reported:
                self._reported.add(message)
                self.on_error(message)
        return items

    def load_all_lenient(self) -> Tuple[List[Tuple[Path, Ticket]], List[str]]:
        """Every readable ticket plus the errors of the unreadable ones; for doctor."""
        items: List[Tuple[Path, Ticket]] = []
        errors: List[str] = []
        for path in self.iter_paths():
            try:
                items.append((path, load_ticket(path)))
            except TicketError as exc:
                errors.append(str(exc))
        return items, errors

    def by_id(self) -> Dict[str, Ticket]:
        return {ticket.id: ticket for _, ticket in self.load_all()}

    def paths_by_id(self) -> Dict[str, Tuple[Path, Ticket]]:
        return {ticket.id: (path, ticket) for path, ticket in self.load_all()}

    def existing_ids(self) -> set:
        return {path.stem for path in self.iter_paths()}

    def new_id(self) -> str:
        return gen_id(self.existing_ids())

    def find(self, term: str, unique: bool = False) -> Tuple[Path, Ticket]:
        """Find by id, or by case-insensitive substring of the id. An exact
        match always wins; with unique=True an ambiguous substring is an
        error (mutating commands pass it, so nothing is written to the wrong
        ticket)."""
        term_lower = term.strip().lower()
        if not term_lower:
            raise StoreError("empty ticket id")
        matches = [(p, t) for p, t in self.load_all() if term_lower in t.id.lower()]
        if not matches:
            raise StoreError(f"no ticket matches {term!r}")
        exact = [m for m in matches if m[1].id.lower() == term_lower]
        if exact:
            return exact[0]
        if unique and len(matches) > 1:
            ids = ", ".join(t.id for _, t in matches)
            raise StoreError(f"{term!r} is ambiguous: {ids}. Pass a full ticket id.")
        matches.sort(key=lambda m: m[1].id)
        return matches[0]

    def find_by_origin(self, origin: str) -> Optional[Tuple[Path, Ticket]]:
        for path, ticket in self.load_all():
            if ticket.origin == origin:
                return path, ticket
        return None

    def stray_files(self) -> List[Path]:
        """Temp files and non-ticket files inside status folders; for doctor."""
        strays: List[Path] = []
        for status in STATUS_FOLDERS:
            folder = self.root / status
            if not folder.is_dir():
                continue
            folders = [folder] + ([p for p in folder.iterdir() if p.is_dir()] if status == "closed" else [])
            for sub in folders:
                for entry in sub.iterdir():
                    if entry.is_dir():
                        if status != "closed" or sub != folder:
                            strays.append(entry)
                        continue
                    if entry.name.startswith(TMP_PREFIX) or entry.suffix != ".md":
                        strays.append(entry)
        return strays

    # -- writing ----------------------------------------------------------

    def create(self, ticket: Ticket) -> Path:
        """Write a new ticket into the folder of its status, exclusively."""
        if not ticket.created:
            ticket.created = now()
        if not ticket.updated:
            ticket.updated = ticket.created
        path = self.status_dir(ticket.status, ticket.closed) / f"{ticket.id}.md"
        create_exclusive(ticket.to_markdown(), path)
        return path

    def save(self, path: Path, ticket: Ticket) -> None:
        save_ticket(ticket, path)

    def move(self, path: Path, ticket: Ticket, new_status: str) -> Path:
        ticket.updated = now()
        return move_ticket(path, ticket, self.root, new_status)

    def claim(self, path: Path, ticket: Ticket, agent_id: str) -> Path:
        return claim_file(path, ticket, self.root, agent_id)

    # -- work selection ---------------------------------------------------

    @staticmethod
    def workable(tickets: Iterable[Ticket], by_id: Dict[str, Ticket]) -> List[Ticket]:
        """Open tickets whose dependencies are all closed. A dependency that
        does not exist counts as unmet, so a typo never unlocks work."""
        result = []
        for ticket in tickets:
            if ticket.status != "open":
                continue
            if all(dep in by_id and by_id[dep].status == "closed" for dep in ticket.depends_on):
                result.append(ticket)
        return result

    @staticmethod
    def order_for_agent(tickets: Iterable[Ticket], agent: Agent) -> List[Ticket]:
        """Own level first, then descending level; within a level by priority,
        then age (created), then id."""
        def key(t: Ticket):
            rank = t.skill_rank()
            level_key = 0 if rank == agent.rank else agent.rank - rank
            return (level_key, t.priority_sort_key(), t.created, t.id)
        return sorted(tickets, key=key)

    def next_for(
        self,
        agent: Agent,
        count: int = 1,
        epic: Optional[str] = None,
        domain: Optional[str] = None,
        ignore_floor: bool = False,
    ) -> Tuple[List[Ticket], int]:
        """(tickets, below_floor). Workable tickets at or below the agent's
        level, ordered; those under the agent's floor are left out unless
        ignore_floor, and their count is returned so the caller can say so."""
        by_id = self.by_id()
        candidates = self.workable(by_id.values(), by_id)
        candidates = [t for t in candidates if t.skill_rank() <= agent.rank]
        if epic:
            candidates = [t for t in candidates if t.epic == epic]
        if domain:
            candidates = [t for t in candidates if t.domain == domain]
        below = [t for t in candidates if t.skill_rank() < agent.floor_rank]
        if not ignore_floor:
            candidates = [t for t in candidates if t.skill_rank() >= agent.floor_rank]
        ordered = self.order_for_agent(candidates, agent)
        return ordered[: max(1, count)], len(below)

    def topological(self, tickets: List[Ticket]) -> List[Ticket]:
        """Dependencies before dependants; stable otherwise."""
        by_id = {t.id: t for t in tickets}
        seen: set = set()
        order: List[Ticket] = []

        def visit(ticket: Ticket, chain: set) -> None:
            if ticket.id in seen or ticket.id in chain:
                return
            chain.add(ticket.id)
            for dep in ticket.depends_on:
                if dep in by_id:
                    visit(by_id[dep], chain)
            chain.discard(ticket.id)
            seen.add(ticket.id)
            order.append(ticket)

        for ticket in tickets:
            visit(ticket, set())
        return order

    # -- events -----------------------------------------------------------

    @property
    def events_path(self) -> Path:
        return self.root / EVENTS_FILE

    def log_event(
        self,
        action: str,
        ticket_id: Optional[str],
        agent: Optional[str],
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> Event:
        event = Event(now(), agent, action, ticket_id, from_status, to_status, detail)
        line = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=False)
        with open(self.events_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return event

    def read_events(
        self, ticket_id: Optional[str] = None, since: Optional[str] = None, count: Optional[int] = None
    ) -> List[Event]:
        if not self.events_path.is_file():
            return []
        events: List[Event] = []
        with open(self.events_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = Event.from_dict(data)
                if ticket_id and event.ticket != ticket_id:
                    continue
                if since and event.at < since:
                    continue
                events.append(event)
        if count is not None and count > 0:
            events = events[-count:]
        return events

    def last_event_at(self) -> Dict[str, str]:
        """ticket id -> timestamp of its most recent event."""
        last: Dict[str, str] = {}
        for event in self.read_events():
            if event.ticket:
                last[event.ticket] = event.at
        return last

    def stale(self, hours: int) -> List[Tuple[Ticket, str, float]]:
        """(ticket, last activity, hours idle) for live tickets idle longer
        than `hours`. Activity is the later of the ticket's `updated` and its
        last event."""
        last = self.last_event_at()
        result = []
        current = datetime.now()
        for _, ticket in self.load_all():
            if ticket.status not in ("in_progress", "review"):
                continue
            stamp = max(filter(None, [ticket.updated, last.get(ticket.id)]), default=ticket.created)
            try:
                then = datetime.fromisoformat(stamp)
            except ValueError:
                continue
            idle = (current - then).total_seconds() / 3600.0
            if idle >= hours:
                result.append((ticket, stamp, idle))
        result.sort(key=lambda item: -item[2])
        return result
