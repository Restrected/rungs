"""`rungs import-arbite`: convert an existing `.arbite/` store, and the lenient
arbite-markdown reader that `rungs intake` reuses for `.md` files in the inbox.

The old store was written by PyYAML, which folds a long single-quoted title
over several indented lines and writes block lists at the same indentation as
their key. Neither is in the strict subset `miniyaml` accepts, so the reader
tries `miniyaml` first and falls back to a tolerant line parser. Nothing here
writes to the arbite store: it is only ever read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import miniyaml
from .roster import Roster, RosterError
from .store import Store
from .ticket import (
    ID_PATTERN, Ticket, TicketError, clean_text, now, one_line, render_body,
    split_sections, validate,
)

__all__ = [
    "ARBITE_STATUS_MAP", "TIER_TO_SKILL", "TYPE_TO_KIND", "ArbiteDoc", "ArbiteError",
    "parse_arbite", "read_arbite_file", "intake_request_from_arbite", "map_ticket",
    "iter_arbite_tickets", "run_import", "register",
]

# Folders of an arbite store that hold tickets, plus closed/<month>/. Anything
# else is skipped: `planning/` and whatever is nested under it is not work, and
# `agents/` is handled separately by run_import.
ARBITE_TICKET_FOLDERS = ["raw", "open", "in_progress", "blocked", "shelved", "wishlist"]

ARBITE_STATUS_MAP = {
    "raw": "triage",
    "open": "open",
    "in_progress": "in_progress",
    "blocked": "blocked",
    "shelved": "shelved",
    "closed": "closed",
}
TIER_TO_SKILL = {"low": "low", "medium": "medium", "high": "high", "frontier": "xhigh"}
TYPE_TO_KIND = {
    "bug": "fix",
    "feature": "implement",
    "refactor": "implement",
    "chore": "chore",
    "memo": "doc",
    "wish": "implement",
}

_KEY_LINE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s*(.*))?$")
_INT_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}


class ArbiteError(Exception):
    pass


@dataclass
class ArbiteDoc:
    fields: Dict[str, Any]
    body: str
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Lenient front matter reader
# --------------------------------------------------------------------------


def _scan_quoted(text: str, quote: str) -> Tuple[str, bool]:
    """Read a quoted scalar body (the text after the opening quote). Returns
    (value, closed)."""
    out: List[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), True
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < len(text):
            out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        if ch == '"':
            return "".join(out), True
        out.append(ch)
        i += 1
    return "".join(out), False


def _scalar(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if text[0] in "'\"":
        value, _closed = _scan_quoted(text[1:], text[0])
        return value
    idx = text.find(" #")
    if idx != -1:
        text = text[:idx].rstrip()
    if text in ("null", "~", ""):
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if _INT_RE.match(text):
        return int(text)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in inner.split(",") if part.strip()]
    return text


def _lenient_front_matter(front: str) -> Tuple[Dict[str, Any], List[str]]:
    """Parse a PyYAML-written front matter without PyYAML: flat `key: value`
    lines, block lists at any indentation, and single/double quoted scalars
    folded over several lines (the line break becomes a space, as YAML does)."""
    data: Dict[str, Any] = {}
    warnings: List[str] = []
    lines = front.splitlines()
    current_key: Optional[str] = None
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if stripped == "-" or stripped.startswith("- "):
            item = stripped[1:].strip()
            if current_key is None:
                warnings.append("front matter: list item with no field: {0!r}".format(stripped))
            else:
                if not isinstance(data.get(current_key), list):
                    data[current_key] = []
                if item:
                    data[current_key].append(_scalar(item))
            i += 1
            continue
        match = _KEY_LINE.match(stripped)
        if not match:
            warnings.append("front matter: ignored line {0!r}".format(stripped))
            i += 1
            continue
        key = match.group(1)
        rest = (match.group(2) or "").strip()
        current_key = None
        if rest == "":
            data[key] = None
            current_key = key
            i += 1
            continue
        if rest[0] in "'\"":
            quote = rest[0]
            buffer = rest[1:]
            j = i
            while True:
                value, closed = _scan_quoted(buffer, quote)
                if closed:
                    data[key] = value
                    break
                j += 1
                if j >= len(lines):
                    warnings.append(
                        "front matter: unterminated quoted value for {0!r}".format(key)
                    )
                    data[key] = value
                    break
                buffer = buffer + " " + lines[j].strip()
            i = j + 1
            continue
        data[key] = _scalar(rest)
        i += 1
    return data, warnings


def _split_front_matter(text: str) -> Tuple[str, str]:
    """Split on lines that are exactly `---`, never on the substring."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ArbiteError("file does not start with a '---' line")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1:]).lstrip("\n")
    raise ArbiteError("front matter has no closing '---' line")


def parse_arbite(text: str) -> ArbiteDoc:
    """Front matter fields plus the body of an arbite ticket file."""
    front, body = _split_front_matter(text)
    warnings: List[str] = []
    data: Optional[Dict[str, Any]] = None
    try:
        parsed = miniyaml.loads(front)
        if isinstance(parsed, dict):
            data = parsed
    except miniyaml.YamlError:
        data = None
    if data is None:
        data, warnings = _lenient_front_matter(front)
    return ArbiteDoc(fields=data, body=body, warnings=warnings)


def read_arbite_file(path: Path) -> ArbiteDoc:
    try:
        return parse_arbite(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ArbiteError("{0}: {1}".format(path, exc))
    except ArbiteError as exc:
        raise ArbiteError("{0}: {1}".format(path, exc))


# --------------------------------------------------------------------------
# Body and field mapping
# --------------------------------------------------------------------------


def _body_sections(body: str) -> Tuple[str, str, Dict[str, str]]:
    """(goal text, notes text, other sections) from an arbite body."""
    sections = split_sections(body or "")
    goal_parts: List[str] = []
    preamble = sections.get("", "").strip()
    if preamble:
        goal_parts.append(preamble)
    description = sections.get("Description", "").strip()
    if description:
        goal_parts.append(description)
    notes = sections.get("Notes", "").strip()
    others = {
        name: text
        for name, text in sections.items()
        if name and name not in ("Description", "Notes")
    }
    return "\n\n".join(goal_parts), notes, others


def _str_field(fields: Dict[str, Any], name: str, limit: int) -> Optional[str]:
    value = fields.get(name)
    if value is None:
        return None
    text = one_line(str(value), limit)
    return text or None


def _tags(fields: Dict[str, Any]) -> List[str]:
    raw = fields.get("tags")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    out: List[str] = []
    for item in raw:
        tag = one_line(str(item), 40)
        if tag and tag not in out:
            out.append(tag)
    return out


def _timestamp(fields: Dict[str, Any], name: str) -> Optional[str]:
    value = fields.get(name)
    if value is None:
        return None
    text = one_line(str(value))
    return text if _DATE_RE.match(text) else None


def intake_request_from_arbite(doc: ArbiteDoc, default_key: str = "") -> Dict[str, Any]:
    """An arbite ticket as an intake request, so `.md` files dropped in the
    inbox go through exactly the same validation as JSON ones."""
    fields = doc.fields
    key = one_line(str(fields.get("id") or default_key or ""), 120)
    if not key:
        raise ArbiteError("arbite ticket has no id")
    goal, notes, others = _body_sections(doc.body)
    parts = [goal] if goal.strip() else []
    for name, text in others.items():
        if text.strip():
            parts.append("### {0}\n{1}".format(name, text.strip()))
    if notes.strip():
        parts.append("### Notes carried over from arbite\n{0}".format(notes.strip()))
    request: Dict[str, Any] = {
        "source": "arbite",
        "key": key,
        "title": _str_field(fields, "title", 200) or "arbite ticket {0}".format(key),
        "goal": "\n\n".join(parts) or "(the arbite ticket had no description)",
    }
    kind = TYPE_TO_KIND.get(str(fields.get("type") or "").strip())
    if kind:
        request["kind"] = kind
    skill = TIER_TO_SKILL.get(str(fields.get("tier") or "").strip())
    if skill:
        request["skill"] = skill
    priority = fields.get("priority")
    if isinstance(priority, int) and not isinstance(priority, bool):
        request["priority"] = priority
    for name in ("domain", "epic"):
        value = _str_field(fields, name, 80)
        if value:
            request[name] = value
    tags = _tags(fields)
    if tags:
        request["tags"] = tags
    return request


def map_ticket(
    doc: ArbiteDoc, folder: str, roster: Roster, default_skill: str, default_kind: str
) -> Tuple[Ticket, List[str]]:
    """One arbite ticket as a rungs Ticket. Returns (ticket, warnings)."""
    fields = doc.fields
    warnings = list(doc.warnings)
    ticket_id = one_line(str(fields.get("id") or ""))
    if not ID_PATTERN.match(ticket_id):
        raise ArbiteError("id {0!r} is not a usable ticket id".format(ticket_id))

    tier = str(fields.get("tier") or "").strip()
    skill = TIER_TO_SKILL.get(tier)
    if skill is None:
        if tier:
            warnings.append("{0}: tier {1!r} is not a known arbite tier; using {2}".format(
                ticket_id, tier, default_skill))
        skill = default_skill
    kind_raw = str(fields.get("type") or "").strip()
    kind = TYPE_TO_KIND.get(kind_raw)
    if kind is None:
        if kind_raw:
            warnings.append("{0}: type {1!r} is not a known arbite type; using {2}".format(
                ticket_id, kind_raw, default_kind))
        kind = default_kind

    tags = _tags(fields)
    if folder == "wishlist":
        status = "shelved"
        if "wishlist" not in tags:
            tags.append("wishlist")
    else:
        raw_status = str(fields.get("status") or "").strip()
        status = ARBITE_STATUS_MAP.get(raw_status)
        if status is None:
            status = ARBITE_STATUS_MAP.get(folder, "triage")
            if raw_status:
                warnings.append("{0}: status {1!r} is not a known arbite status; using the "
                                "{2}/ folder -> {3}".format(ticket_id, raw_status, folder, status))

    created = _timestamp(fields, "created") or now()
    updated = _timestamp(fields, "updated") or created
    closed = _timestamp(fields, "closed")
    assignee = _str_field(fields, "assignee", 80)
    note: Optional[str] = None
    if assignee and assignee not in roster.agents:
        if status == "in_progress":
            warnings.append(
                "{0}: assignee {1!r} is not in rungs.yaml; imported as open and unassigned".format(
                    ticket_id, assignee)
            )
            note = ("imported from arbite: it was in progress for {0!r}, which is not in "
                    "rungs.yaml, so it is open and unassigned".format(assignee))
            status = "open"
            assignee = None
        else:
            warnings.append("{0}: assignee {1!r} is not in rungs.yaml; dropped".format(
                ticket_id, assignee))
            assignee = None
    if status not in ("in_progress", "review"):
        assignee = None

    blocked_by = _str_field(fields, "blocked_by", 200)
    if status == "blocked" and not blocked_by:
        blocked_by = "imported from arbite with no reason recorded"
    if status == "closed" and not closed:
        closed = updated or created
        warnings.append("{0}: closed ticket had no closed timestamp; using {1}".format(
            ticket_id, closed))
    if status != "closed":
        closed = None

    depends_on: List[str] = []
    raw_deps = fields.get("depends_on") or []
    if not isinstance(raw_deps, list):
        raw_deps = [raw_deps]
    for dep in raw_deps:
        dep_id = one_line(str(dep))
        if ID_PATTERN.match(dep_id):
            if dep_id != ticket_id and dep_id not in depends_on:
                depends_on.append(dep_id)
        elif dep_id:
            warnings.append("{0}: depends_on entry {1!r} is not a ticket id; dropped".format(
                ticket_id, dep_id))

    priority = fields.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        priority = None

    title = _str_field(fields, "title", 200) or "arbite ticket {0}".format(ticket_id)
    goal, notes, others = _body_sections(doc.body)
    sections: Dict[str, str] = {"Goal": clean_text(goal), "Notes": clean_text(notes)}
    for name, text in others.items():
        sections[name] = clean_text(text)

    ticket = Ticket(
        id=ticket_id,
        title=title,
        status=status,
        kind=kind,
        skill=skill,
        domain=_str_field(fields, "domain", 80),
        epic=_str_field(fields, "epic", 80),
        priority=priority,
        tags=tags,
        scope=[],
        assignee=assignee,
        depends_on=depends_on,
        blocked_by=blocked_by,
        origin="arbite:{0}".format(ticket_id),
        created=created,
        updated=updated,
        closed=closed,
        body=render_body(sections),
    )
    if note:
        ticket.append_note("import", note)
    problems = validate(ticket)
    if problems:
        raise ArbiteError("{0}: {1}".format(ticket_id, "; ".join(problems)))
    return ticket, warnings


# --------------------------------------------------------------------------
# Walking an arbite store
# --------------------------------------------------------------------------


def iter_arbite_tickets(root: Path) -> List[Tuple[Path, str]]:
    """(path, folder) for every ticket file in an arbite store, ordered."""
    found: List[Tuple[Path, str]] = []
    for name in ARBITE_TICKET_FOLDERS:
        folder = root / name
        if folder.is_dir():
            for path in sorted(folder.glob("*.md")):
                found.append((path, name))
    closed = root / "closed"
    if closed.is_dir():
        for month in sorted(p for p in closed.iterdir() if p.is_dir()):
            for path in sorted(month.glob("*.md")):
                found.append((path, "closed"))
    return found


# --------------------------------------------------------------------------
# The import
# --------------------------------------------------------------------------


def run_import(ctx, store: Store, roster: Roster, source: Path, dry_run: bool = False) -> dict:
    from . import docs
    from .cli import RungsError
    from .ticket import AGENT_ID_PATTERN, write_atomic

    source = source.resolve()
    if not source.is_dir():
        raise RungsError("no arbite store at {0}".format(source))

    existing_ids = store.existing_ids()
    existing_origins = {t.origin for _, t in store.load_all() if t.origin}
    files = iter_arbite_tickets(source)

    imported: List[dict] = []
    skipped: List[dict] = []
    warnings: List[str] = []
    by_status: Dict[str, int] = {}
    tickets: List[Ticket] = []

    for path, folder in files:
        try:
            doc = read_arbite_file(path)
        except ArbiteError as exc:
            warnings.append(str(exc))
            continue
        raw_id = one_line(str(doc.fields.get("id") or ""))
        if raw_id and (raw_id in existing_ids or "arbite:{0}".format(raw_id) in existing_origins):
            skipped.append({"id": raw_id, "file": str(path), "reason": "already in the store"})
            continue
        try:
            ticket, ticket_warnings = map_ticket(
                doc, folder, roster, roster.intake.default_skill, roster.intake.default_kind
            )
        except (ArbiteError, TicketError) as exc:
            warnings.append("{0}: {1}".format(path, exc))
            continue
        warnings.extend(ticket_warnings)
        tickets.append(ticket)
        imported.append({
            "id": ticket.id,
            "file": str(path),
            "folder": folder,
            "arbite_status": str(doc.fields.get("status") or folder),
            "arbite_type": str(doc.fields.get("type") or ""),
            "arbite_tier": str(doc.fields.get("tier") or ""),
            "status": ticket.status,
            "kind": ticket.kind,
            "skill": ticket.skill,
            "title": ticket.title,
        })
        by_status[ticket.status] = by_status.get(ticket.status, 0) + 1

    known_ids = existing_ids | {t.id for t in tickets}
    for ticket in tickets:
        for dep in ticket.depends_on:
            if dep not in known_ids:
                warnings.append("{0}: depends_on {1} does not exist in either store".format(
                    ticket.id, dep))

    agent_files: List[str] = []
    agents_dir = source / "agents"
    if agents_dir.is_dir():
        for path in sorted(agents_dir.glob("*.md")):
            agent_id = path.stem
            # The name comes from a folder the tool does not own, so it is
            # checked here and again by store.agent_file before it is a path.
            try:
                if not AGENT_ID_PATTERN.match(agent_id):
                    raise RosterError("not a valid agent id")
                dest = store.agent_file(agent_id)
            except RosterError as exc:
                warnings.append("agents/{0}: {1}; not copied".format(path.name, exc))
                continue
            if dest.exists():
                continue
            agent_files.append(agent_id)
            if dry_run:
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                warnings.append("agents/{0}: {1}".format(path.name, exc))
                agent_files.pop()
                continue
            header = docs.agent_header(agent_id, roster.agents.get(agent_id), roster)
            write_atomic(header + "\n" + body.strip() + "\n", dest)

    if not dry_run:
        for ticket in tickets:
            try:
                store.create(ticket)
            except TicketError as exc:
                warnings.append("{0}: {1}".format(ticket.id, exc))
                continue
            store.log_event(
                "import", ticket.id, None, from_status=None, to_status=ticket.status,
                detail=ticket.origin,
            )

    return {
        "source": str(source),
        "dry_run": dry_run,
        "imported": imported,
        "by_status": by_status,
        "skipped": skipped,
        "agent_files": agent_files,
        "warnings": warnings,
    }


def _print_summary(ctx, summary: dict) -> None:
    imported = summary["imported"]
    if summary["dry_run"]:
        ctx.out("dry run: nothing was written")
        if imported:
            ctx.out("{0:<10} {1:<26} {2}".format("id", "arbite", "rungs"))
            for row in imported:
                ctx.out("{0:<10} {1:<26} {2}".format(
                    row["id"],
                    "{0}/{1}/{2}".format(row["arbite_status"] or "-", row["arbite_type"] or "-",
                                         row["arbite_tier"] or "-"),
                    "{0}/{1}/{2}".format(row["status"], row["kind"], row["skill"]),
                ))
    ctx.out("{0} ticket(s) {1} from {2}".format(
        len(imported), "would be imported" if summary["dry_run"] else "imported", summary["source"]))
    for status in sorted(summary["by_status"]):
        ctx.out("  {0}: {1}".format(status, summary["by_status"][status]))
    if summary["skipped"]:
        ctx.out("{0} skipped (already in the store): {1}".format(
            len(summary["skipped"]), ", ".join(row["id"] for row in summary["skipped"])))
    if summary["agent_files"]:
        ctx.out("{0} agent file(s) {1}: {2}".format(
            len(summary["agent_files"]),
            "would be copied" if summary["dry_run"] else "copied",
            ", ".join(summary["agent_files"])))
    if summary["warnings"]:
        ctx.out("warnings:")
        for warning in summary["warnings"]:
            ctx.out("  - {0}".format(warning))


def _import_arbite(args, ctx) -> Optional[int]:
    store = ctx.store
    roster = ctx.roster
    source = Path(args.path).expanduser() if args.path else store.project_root / ".arbite"
    if not source.is_absolute():
        source = (ctx.cwd / source).resolve()
    summary = run_import(ctx, store, roster, source, dry_run=bool(args.dry_run))
    if ctx.json_output:
        ctx.emit_json(summary)
    else:
        _print_summary(ctx, summary)
    return 0


def register(subparsers, ctx) -> None:
    parser = ctx.command(
        subparsers, "import-arbite", _import_arbite,
        help="convert an existing .arbite/ store into rungs tickets",
        description=(
            "Read an arbite store (default: <project>/.arbite) and create one rungs ticket per\n"
            "arbite ticket, keeping the id, the tags, the priority, the dependencies and the\n"
            "epic. tier low/medium/high/frontier becomes skill low/medium/high/xhigh; type\n"
            "bug/feature/refactor/chore/memo/wish becomes kind fix/implement/implement/chore/\n"
            "doc/implement; status raw becomes triage and the wishlist/ folder becomes shelved\n"
            "with a 'wishlist' tag. '## Description' becomes the Goal section and '## Notes' is\n"
            "kept as Notes. Ids already in the rungs store are skipped. The arbite store is only\n"
            "read, never changed."
        ),
    )
    parser.add_argument(
        "path", nargs="?", metavar="PATH",
        help="the arbite store to read (default: <project>/.arbite)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the mapping and write nothing",
    )
    ctx.add_json_option(parser)
