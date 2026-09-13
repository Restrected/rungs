"""Rendering: tables, the board, and the reports `verify` and `doctor` print.

Nothing here reads the store or decides policy. It turns tickets, events and
check results into the plain text a person reads in a terminal; the JSON form
of every command is built by the command itself from `Ticket.to_dict`.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .ticket import Ticket

__all__ = [
    "BOARD_ORDER", "TICKET_COLUMNS", "truncate", "table", "ticket_rows", "ticket_table",
    "board", "agents_table", "events_lines", "stale_table", "deps_report",
    "verify_report", "doctor_report", "plan_table",
]

BOARD_ORDER = ["triage", "open", "in_progress", "review", "blocked", "shelved"]
TICKET_COLUMNS = ["id", "skill", "kind", "pri", "status", "assignee", "title"]
TITLE_WIDTH = 70
NONE = "-"


def truncate(text: str, width: int = TITLE_WIDTH) -> str:
    text = "" if text is None else str(text)
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A plain text table: left aligned columns, two spaces between them."""
    headers = [str(h) for h in headers]
    body = [[("" if cell is None else str(cell)) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in body:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(cell))
    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()
    out = [line(headers), line(["-" * w for w in widths])]
    out.extend(line(row) for row in body)
    return "\n".join(out)


def ticket_rows(tickets: Iterable[Ticket]) -> List[List[str]]:
    rows = []
    for ticket in tickets:
        rows.append([
            ticket.id,
            ticket.skill,
            ticket.kind,
            NONE if ticket.priority is None else str(ticket.priority),
            ticket.status,
            ticket.assignee or NONE,
            truncate(ticket.title),
        ])
    return rows


def ticket_table(tickets: Iterable[Ticket]) -> str:
    return table(TICKET_COLUMNS, ticket_rows(tickets))


def board(tickets: Iterable[Ticket], statuses: Optional[Sequence[str]] = None) -> str:
    """Tickets grouped by status, each row badged with its skill level."""
    statuses = list(statuses or BOARD_ORDER)
    grouped: Dict[str, List[Ticket]] = {name: [] for name in statuses}
    for ticket in tickets:
        if ticket.status in grouped:
            grouped[ticket.status].append(ticket)
    out: List[str] = []
    for name in statuses:
        items = grouped[name]
        out.append(f"{name} ({len(items)})")
        if not items:
            out.append("  (none)")
        for ticket in items:
            holder = f" [{ticket.assignee}]" if ticket.assignee else ""
            out.append(f"  [{ticket.skill}] {ticket.id}  {truncate(ticket.title, 60)}{holder}")
        out.append("")
    return "\n".join(out).rstrip("\n")


def agents_table(agents: Sequence, held: Dict[str, List[Ticket]]) -> str:
    rows = []
    for agent in agents:
        tickets = held.get(agent.id, [])
        rows.append([
            agent.id,
            agent.skill,
            str(agent.rank),
            agent.role,
            agent.floor or NONE,
            ", ".join(t.id for t in tickets) or NONE,
        ])
    return table(["agent", "skill", "rank", "role", "floor", "current"], rows)


def events_lines(events: Iterable) -> List[str]:
    lines = []
    for event in events:
        move = ""
        if event.from_status or event.to_status:
            move = f" {event.from_status or NONE}→{event.to_status or NONE}"
        detail = f" ({event.detail})" if event.detail else ""
        lines.append(f"{event.at} {event.agent or NONE} {event.action} {event.ticket or NONE}{move}{detail}")
    return lines


def stale_table(rows: Sequence[Tuple[Ticket, str, float]]) -> str:
    body = []
    for ticket, stamp, idle in rows:
        body.append([
            ticket.id, ticket.status, ticket.assignee or NONE, stamp,
            f"{idle:.1f}", truncate(ticket.title, 50),
        ])
    return table(["id", "status", "assignee", "last activity", "idle h", "title"], body)


def deps_report(ticket: Ticket, depends: Sequence[Ticket], missing: Sequence[str],
                blocks: Sequence[Ticket]) -> str:
    out = [f"{ticket.id} ({ticket.status}): {ticket.title}", "", "depends on:"]
    if not depends and not missing:
        out.append("  (nothing)")
    for dep in depends:
        mark = "done" if dep.status == "closed" else "WAITING"
        out.append(f"  {dep.id}  {dep.status:<12} {mark:<8} {truncate(dep.title, 50)}")
    for dep_id in missing:
        out.append(f"  {dep_id}  {'missing':<12} {'WAITING':<8} (no such ticket)")
    out.append("")
    out.append("blocks:")
    if not blocks:
        out.append("  (nothing)")
    for other in blocks:
        out.append(f"  {other.id}  {other.status:<12} {truncate(other.title, 50)}")
    return "\n".join(out)


def verify_report(result, tree_used: bool) -> str:
    out = [f"verify {result.ticket}", ""]
    out.append("scope:")
    if result.scope:
        for entry in result.scope:
            out.append(f"  {entry}")
    else:
        out.append("  (empty: this ticket may change no files)")
    out.append("")
    out.append(f"checked {len(result.checked)} path(s):")
    for name in ("declared", "tree", "explicit"):
        paths = result.sources.get(name)
        if not paths:
            continue
        label = {
            "declared": "declared at submit",
            "tree": "from the git tree (may include another agent's concurrent edits)",
            "explicit": "given with --files",
        }[name]
        out.append(f"  {name} — {label}")
        for path in paths:
            mark = "OUTSIDE" if path in result.outside else "ok"
            out.append(f"    {mark:<8} {path}")
    if not result.checked:
        out.append("  (nothing to check)")
    if result.declared_not_changed:
        out.append("")
        out.append("declared at submit but not changed in the tree:")
        for path in result.declared_not_changed:
            out.append(f"  {path}")
    out.append("")
    if result.outside:
        out.append(f"OUTSIDE SCOPE ({len(result.outside)}):")
        for path in result.outside:
            out.append(f"  {path}")
    else:
        out.append("every checked path is inside the scope")
    if tree_used and not result.sources.get("tree"):
        out.append("(git reported no changed paths)")
    return "\n".join(out)


def doctor_report(problems: Sequence[dict], fixed: Sequence[str]) -> str:
    out: List[str] = []
    if fixed:
        out.append(f"repaired {len(fixed)}:")
        out.extend(f"  {line}" for line in fixed)
        out.append("")
    if not problems:
        out.append("no problems found")
        return "\n".join(out)
    out.append(f"{len(problems)} problem(s):")
    for problem in problems:
        where = problem.get("ticket") or problem.get("path") or ""
        where = f"{where}: " if where else ""
        fixable = " [--fix repairs this]" if problem.get("fixable") else ""
        out.append(f"  [{problem['kind']}] {where}{problem['message']}{fixable}")
    return "\n".join(out)


def plan_table(plans: Sequence[dict]) -> str:
    rows = [[
        p.get("slug", ""), p.get("status") or NONE, str(p.get("tickets", 0)),
        str(p.get("cut", 0)), p.get("epic") or NONE, truncate(p.get("title") or "", 50),
    ] for p in plans]
    return table(["slug", "status", "blocks", "cut", "epic", "title"], rows)
