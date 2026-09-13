"""Ticket model: vocabularies, front matter, body sections, atomic file io.

A ticket is one markdown file: a front matter in the miniyaml subset between
two lines that are exactly ``---``, then a body of ``## `` sections in a fixed
order. The folder the file sits in is its status; every writer here keeps the
``status`` field in step with the folder.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import miniyaml

__all__ = [
    "STATUSES", "FLAT_STATUS_DIRS", "LIVE_STATUSES", "KINDS", "SKILLS", "VERDICTS",
    "FIELD_ORDER", "SECTIONS", "SECTION_KEYS", "SETTABLE_FIELDS", "SETTABLE_SECTIONS",
    "ID_PATTERN", "AGENT_ID_PATTERN", "TicketError", "Ticket", "normalise_skill",
    "skill_rank", "parse_ticket", "load_ticket", "save_ticket", "create_exclusive",
    "write_atomic", "move_ticket", "claim_file", "gen_id", "now", "status_dir",
    "validate", "find_cycles", "clean_text", "one_line", "TMP_PREFIX",
]

# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------

STATUSES = ["triage", "open", "in_progress", "review", "blocked", "shelved", "closed"]
FLAT_STATUS_DIRS = {"triage", "open", "in_progress", "review", "blocked", "shelved"}
# Statuses whose scope is held: another ticket overlapping one of these is refused.
LIVE_STATUSES = {"in_progress", "review"}

KINDS = ["design", "implement", "fix", "review", "check", "research", "doc", "chore"]

# The ladder, lowest first. Rank is index + 1.
SKILLS = ["very-low", "low", "medium", "high", "xhigh"]
SKILL_ALIASES = {
    "very-low": "very-low", "very_low": "very-low", "verylow": "very-low", "vlow": "very-low",
    "xlow": "very-low", "1": "very-low",
    "low": "low", "2": "low",
    "medium": "medium", "med": "medium", "mid": "medium", "3": "medium",
    "high": "high", "4": "high",
    "xhigh": "xhigh", "very-high": "xhigh", "very_high": "xhigh", "veryhigh": "xhigh",
    "vhigh": "xhigh", "extra-high": "xhigh", "5": "xhigh",
}

VERDICTS = ["accepted", "returned"]

FIELD_ORDER = [
    "id", "title", "status", "kind", "skill", "domain", "epic", "plan", "priority",
    "tags", "scope", "assignee", "reviewer", "depends_on", "blocked_by", "origin",
    "verdict", "review_rounds", "files", "created", "updated", "submitted", "closed",
]

SECTIONS = [
    "Goal",
    "Decisions already made",
    "Constraints",
    "Acceptance criteria",
    "Evidence required",
    "Evidence",
    "Review",
    "Notes",
]
SECTION_KEYS = {
    "goal": "Goal",
    "decisions": "Decisions already made",
    "constraints": "Constraints",
    "acceptance": "Acceptance criteria",
    "evidence_required": "Evidence required",
    "evidence": "Evidence",
    "review": "Review",
    "notes": "Notes",
}
SETTABLE_FIELDS = ["title", "kind", "skill", "domain", "epic", "priority", "tags", "scope"]
SETTABLE_SECTIONS = ["goal", "decisions", "constraints", "acceptance", "evidence_required"]

ID_PATTERN = re.compile(r"^[a-z]{1,8}-[0-9a-f]{4,8}$")
AGENT_ID_PATTERN = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+){1,3}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$")
DATE_FIELDS = ("created", "updated", "submitted", "closed")

TITLE_MAX = 200
NEW_ID_PREFIX = "rg-"
TMP_PREFIX = ".rungs-tmp-"

_HEADING_RE = re.compile(r"^## (.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class TicketError(Exception):
    pass


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------


_STRUCTURAL_HEADING_RE = re.compile(r"^(\s{0,3})(#{1,2})(\s|$)")


def clean_text(text: str) -> str:
    """Multi-line text safe for a ticket body: no control characters, LF only,
    no run of three dashes that another tool could take for a front matter
    boundary, no line that would read as a section heading (`#` or `##`
    outside a fence is escaped), and every code fence closed so pasted text
    cannot swallow the sections that follow it."""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)
    text = re.sub(r"-{3,}", lambda m: "- " * (len(m.group(0)) - 1) + "-", text)
    lines = []
    open_fence = None
    for line in text.split("\n"):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if open_fence is None:
                open_fence = marker
            elif open_fence == marker:
                open_fence = None
        elif open_fence is None and _STRUCTURAL_HEADING_RE.match(line):
            line = _STRUCTURAL_HEADING_RE.sub(lambda m: m.group(1) + "\\" + m.group(2) + m.group(3), line, count=1)
        lines.append(line)
    if open_fence is not None:
        lines.append(open_fence)
    return "\n".join(lines).strip()


def one_line(text: str, limit: Optional[int] = None) -> str:
    """Text meant for one front matter line or one note line."""
    text = clean_text(text).replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def normalise_skill(value) -> str:
    key = str(value).strip().lower()
    if key not in SKILL_ALIASES:
        raise TicketError(f"unknown skill level {value!r}; use one of {', '.join(SKILLS)}")
    return SKILL_ALIASES[key]


def skill_rank(skill: str) -> int:
    return SKILLS.index(normalise_skill(skill)) + 1


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def gen_id(existing_ids: Iterable[str]) -> str:
    taken = set(existing_ids)
    while True:
        candidate = NEW_ID_PREFIX + uuid.uuid4().hex[:5]
        if candidate not in taken:
            return candidate


# --------------------------------------------------------------------------
# Body sections
# --------------------------------------------------------------------------


def split_sections(body: str) -> Dict[str, str]:
    """Split a body into {heading: text}. Headings inside fenced code blocks
    are ignored. Text before the first heading is stored under ''."""
    sections: Dict[str, List[str]] = {"": []}
    current = ""
    in_fence = None
    for line in body.splitlines():
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if in_fence is None:
                in_fence = marker
            elif in_fence == marker:
                in_fence = None
            sections[current].append(line)
            continue
        heading = _HEADING_RE.match(line) if in_fence is None else None
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    return {name: "\n".join(lines).strip("\n") for name, lines in sections.items()}


def render_body(sections: Dict[str, str]) -> str:
    """Render sections in canonical order, then any extra ones in the order
    given. Every canonical section is always present so the template is
    visible to whoever reads the file."""
    out: List[str] = []
    preamble = sections.get("", "").strip()
    if preamble:
        out.append(preamble)
        out.append("")
    seen = set()
    for name in SECTIONS + [n for n in sections if n and n not in SECTIONS]:
        if name in seen:
            continue
        seen.add(name)
        text = sections.get(name, "").strip("\n")
        out.append(f"## {name}")
        if text.strip():
            out.append(text)
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# Ticket
# --------------------------------------------------------------------------


@dataclass
class Ticket:
    id: str
    title: str
    status: str
    kind: str
    skill: str
    domain: Optional[str] = None
    epic: Optional[str] = None
    plan: Optional[str] = None
    priority: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    scope: List[str] = field(default_factory=list)
    assignee: Optional[str] = None
    reviewer: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    blocked_by: Optional[str] = None
    origin: Optional[str] = None
    verdict: Optional[str] = None
    review_rounds: int = 0
    files: List[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    submitted: Optional[str] = None
    closed: Optional[str] = None
    body: str = ""

    # -- ordering ---------------------------------------------------------

    def priority_sort_key(self) -> float:
        return self.priority if self.priority is not None else float("inf")

    def skill_rank(self) -> int:
        return skill_rank(self.skill)

    # -- sections ---------------------------------------------------------

    def sections(self) -> Dict[str, str]:
        return split_sections(self.body)

    def section(self, name: str) -> str:
        name = SECTION_KEYS.get(name, name)
        return self.sections().get(name, "")

    def set_section(self, name: str, text: str) -> None:
        name = SECTION_KEYS.get(name, name)
        sections = self.sections()
        sections[name] = clean_text(text)
        self.body = render_body(sections)

    def append_to_section(self, name: str, text: str) -> None:
        name = SECTION_KEYS.get(name, name)
        sections = self.sections()
        existing = sections.get(name, "").strip("\n")
        addition = clean_text(text)
        sections[name] = f"{existing}\n\n{addition}" if existing.strip() else addition
        self.body = render_body(sections)

    def append_note(self, agent_id: str, message: str, at: Optional[str] = None) -> None:
        """Append `- <timestamp> <agent>: <message>` to Notes. Continuation
        lines of a multi-line message are indented so the list stays one
        item per note."""
        at = at or now()
        text = clean_text(message)
        lines = text.split("\n") if text else [""]
        entry = f"- {at} {agent_id}: {lines[0]}"
        for extra in lines[1:]:
            entry += f"\n  {extra}"
        sections = self.sections()
        existing = sections.get("Notes", "").strip("\n")
        sections["Notes"] = f"{existing}\n{entry}" if existing.strip() else entry
        self.body = render_body(sections)

    # -- serialisation ----------------------------------------------------

    def to_dict(self, path: Optional[Path] = None) -> dict:
        data = {name: getattr(self, name) for name in FIELD_ORDER}
        data["body"] = self.body
        data["sections"] = {k: v for k, v in self.sections().items() if k}
        if path is not None:
            data["path"] = str(path)
        return data

    def to_markdown(self) -> str:
        data = {}
        for name in FIELD_ORDER:
            value = getattr(self, name)
            if name in ("tags", "scope", "depends_on", "files"):
                value = [one_line(str(v)) for v in (value or [])]
            elif isinstance(value, str):
                value = one_line(value)
            data[name] = value
        front = miniyaml.dumps(data)
        body = render_body(self.sections())
        return f"---\n{front}---\n\n{body}"


def _split_frontmatter(text: str) -> Tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise TicketError("ticket file must start with a '---' line")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            front = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :])
            return front, body.lstrip("\n")
    raise TicketError("ticket front matter has no closing '---' line")


def parse_ticket(text: str) -> Ticket:
    front, body = _split_frontmatter(text)
    try:
        data = miniyaml.loads(front)
    except miniyaml.YamlError as exc:
        raise TicketError(f"front matter: {exc}")
    known = {f.name for f in fields(Ticket)}
    unknown = sorted(k for k in data if k not in known)
    if unknown:
        raise TicketError(f"front matter has unknown fields: {', '.join(unknown)}")
    kwargs = {k: v for k, v in data.items() if k in known}
    for name in ("tags", "scope", "depends_on", "files"):
        value = kwargs.get(name)
        if value is None:
            kwargs[name] = []
        elif not isinstance(value, list):
            raise TicketError(f"front matter field {name} must be a list")
        else:
            kwargs[name] = [str(v) for v in value]
    if kwargs.get("review_rounds") is None:
        kwargs["review_rounds"] = 0
    for name in ("id", "title", "status", "kind", "skill"):
        if name not in kwargs or kwargs[name] is None:
            raise TicketError(f"front matter is missing the required field {name}")
    for name in ("id", "title", "status", "kind", "skill", "domain", "epic", "plan", "assignee",
                 "reviewer", "blocked_by", "origin", "verdict", "created", "updated",
                 "submitted", "closed"):
        if kwargs.get(name) is not None:
            kwargs[name] = str(kwargs[name])
    try:
        return Ticket(body=body, **kwargs)
    except TypeError as exc:
        raise TicketError(f"front matter is not valid: {exc}")


def load_ticket(path: Path) -> Ticket:
    try:
        return parse_ticket(path.read_text(encoding="utf-8"))
    except (TicketError, OSError, UnicodeDecodeError) as exc:
        raise TicketError(f"{path}: {exc}")


def validate(ticket: Ticket) -> List[str]:
    """Problems with a ticket's field values, as messages. Empty means valid."""
    problems: List[str] = []
    if not ID_PATTERN.match(ticket.id or ""):
        problems.append(f"id {ticket.id!r} is not of the form prefix-hex")
    if not ticket.title or not ticket.title.strip():
        problems.append("title is empty")
    elif len(ticket.title) > TITLE_MAX:
        problems.append(f"title is longer than {TITLE_MAX} characters")
    if ticket.status not in STATUSES:
        problems.append(f"status {ticket.status!r} is not one of {', '.join(STATUSES)}")
    if ticket.kind not in KINDS:
        problems.append(f"kind {ticket.kind!r} is not one of {', '.join(KINDS)}")
    if ticket.skill not in SKILLS:
        problems.append(f"skill {ticket.skill!r} is not one of {', '.join(SKILLS)}")
    if ticket.priority is not None and not isinstance(ticket.priority, int):
        problems.append("priority must be an integer")
    if not isinstance(ticket.review_rounds, int) or ticket.review_rounds < 0:
        problems.append("review_rounds must be a non-negative integer")
    if ticket.verdict is not None and ticket.verdict not in VERDICTS:
        problems.append(f"verdict {ticket.verdict!r} is not one of {', '.join(VERDICTS)}")
    for name in ("assignee", "reviewer"):
        value = getattr(ticket, name)
        if value is not None and not AGENT_ID_PATTERN.match(value):
            problems.append(f"{name} {value!r} is not a valid agent id")
    for dep in ticket.depends_on:
        if not ID_PATTERN.match(dep):
            problems.append(f"depends_on entry {dep!r} is not a ticket id")
        if dep == ticket.id:
            problems.append("ticket depends on itself")
    for name in DATE_FIELDS:
        value = getattr(ticket, name)
        if value is not None and not DATE_PATTERN.match(str(value)):
            problems.append(f"{name} {value!r} is not YYYY-MM-DDTHH:MM:SS")
    if ticket.status == "in_progress" and not ticket.assignee:
        problems.append("in_progress ticket has no assignee")
    if ticket.status == "review" and not ticket.assignee:
        problems.append("review ticket has no assignee")
    if ticket.status == "blocked" and not ticket.blocked_by:
        problems.append("blocked ticket has no blocked_by reason")
    if ticket.status == "closed" and not ticket.closed:
        problems.append("closed ticket has no closed timestamp")
    if ticket.status != "closed" and ticket.closed:
        problems.append("ticket has a closed timestamp but is not closed")
    if ticket.origin is not None and ":" not in ticket.origin:
        problems.append("origin must be source:key")
    for entry in ticket.scope:
        if entry.startswith("/") or ".." in entry.split("/"):
            problems.append(f"scope entry {entry!r} must be a relative path inside the project")
    return problems


# --------------------------------------------------------------------------
# File io
# --------------------------------------------------------------------------


def status_dir(status: str, root: Path, closed_date: Optional[str] = None) -> Path:
    if status == "closed":
        month = (closed_date or "")[:7]
        if not re.match(r"^\d{4}-\d{2}$", month):
            month = now()[:7]
        return root / "closed" / month
    if status not in FLAT_STATUS_DIRS:
        raise TicketError(f"unknown status: {status}")
    return root / status


def _write_fd(fd: int, text: str) -> None:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def _tmp_file(directory: Path) -> Tuple[int, Path]:
    """A temp file in `directory` with the mode a normal create would get
    (mkstemp alone gives 0600, which would hide tickets and result files from
    other users of the store)."""
    fd, name = tempfile.mkstemp(prefix=TMP_PREFIX, dir=str(directory))
    os.chmod(name, 0o666 & ~_umask())
    return fd, Path(name)


def write_atomic(text: str, path: Path) -> None:
    """A complete temp file in the same directory, then an atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _tmp_file(path.parent)
    try:
        _write_fd(fd, text)
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def create_exclusive(text: str, path: Path) -> None:
    """Create a new file, failing if it exists. Never overwrites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except FileExistsError:
        raise TicketError(f"{path} already exists")
    try:
        _write_fd(fd, text)
    except BaseException:
        if path.exists():
            path.unlink()
        raise


def save_ticket(ticket: Ticket, path: Path) -> None:
    write_atomic(ticket.to_markdown(), path)


def move_ticket(path: Path, ticket: Ticket, root: Path, new_status: str) -> Path:
    """Move the file to the folder of new_status and rewrite the status field
    in one operation. The temp file is staged in the destination, the source
    is unlinked, then the temp file is renamed: a crash can leave the ticket
    briefly invisible (doctor finds the temp file) but never duplicated."""
    dest_dir = status_dir(new_status, root, closed_date=ticket.closed)
    dest = dest_dir / path.name
    ticket.status = new_status
    if dest == path:
        save_ticket(ticket, dest)
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = _tmp_file(dest_dir)
    try:
        _write_fd(fd, ticket.to_markdown())
        if path.exists():
            path.unlink()
        os.replace(tmp, dest)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    return dest


def claim_file(path: Path, ticket: Ticket, root: Path, agent: str) -> Path:
    """Move into in_progress/ with an exclusive create as the mutex: two agents
    racing for one ticket cannot both win."""
    dest_dir = root / "in_progress"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    ticket.status = "in_progress"
    ticket.assignee = agent
    ticket.updated = now()
    if dest == path:
        save_ticket(ticket, dest)
        return dest
    try:
        fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except FileExistsError:
        raise TicketError(f"ticket {ticket.id} was claimed by another agent first")
    try:
        _write_fd(fd, ticket.to_markdown())
    except BaseException:
        if dest.exists():
            dest.unlink()
        raise
    if path.exists():
        path.unlink()
    return dest


def find_cycles(by_id: Dict[str, Ticket]) -> List[List[str]]:
    """Every depends_on cycle, each as a list of ids. Iterative DFS."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {tid: WHITE for tid in by_id}
    cycles: List[List[str]] = []
    seen = set()
    for start in sorted(by_id):
        if colour[start] != WHITE:
            continue
        colour[start] = GREY
        stack = [(start, iter(by_id[start].depends_on))]
        chain = [start]
        while stack:
            node, deps = stack[-1]
            descended = False
            for dep in deps:
                if dep not in by_id:
                    continue
                if colour[dep] == GREY:
                    cycle = chain[chain.index(dep):]
                    key = tuple(sorted(cycle))
                    if key not in seen:
                        seen.add(key)
                        cycles.append(cycle)
                    continue
                if colour[dep] == WHITE:
                    colour[dep] = GREY
                    stack.append((dep, iter(by_id[dep].depends_on)))
                    chain.append(dep)
                    descended = True
                    break
            if not descended:
                colour[node] = BLACK
                stack.pop()
                chain.pop()
    return cycles
