"""The two checks: `verify` (a change set against a ticket's scope) and
`doctor` (the integrity of the store).

Both print a report and then raise :class:`Problems` (exit 3) when they found
anything, so a script can tell "checked, clean" from "checked, problems" from
"could not run". Nothing here changes a ticket's status: `doctor --fix` only
repairs what is unambiguous — a front matter status that disagrees with the
folder the file sits in, a closed ticket filed under the wrong month, and a
missing `updated` timestamp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import scope, views
from .cli import Context, Problems
from .store import Store
from .ticket import (
    STATUSES, Ticket, find_cycles, move_ticket, now, save_ticket, validate,
)

__all__ = ["register"]


# --------------------------------------------------------------------------
# Small helpers (kept here so the module stands on its own)
# --------------------------------------------------------------------------


def _split_list(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _rel(store: Store, path: Path) -> str:
    try:
        return str(path.relative_to(store.project_root))
    except ValueError:
        return str(path)


def cmd_verify(args, ctx: Context) -> Optional[int]:
    store = ctx.store
    path, ticket = store.find(args.id)
    explicit = _split_list(args.files)
    declared = None if explicit else list(ticket.files)
    tree_files = None
    tree_used = bool(args.tree or args.staged or args.since)
    if tree_used:
        tree_files = scope.git_changed(store.project_root, tree=args.tree,
                                       staged=args.staged, since=args.since)
    result = scope.verify(ticket, declared=declared, tree_files=tree_files, explicit=explicit)
    if ctx.json_output:
        ctx.emit_json(result.to_dict())
    else:
        ctx.out(views.verify_report(result, tree_used))
    if result.outside:
        raise Problems(f"{len(result.outside)} path(s) outside the scope of {ticket.id}")
    return None


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def _folder_status(store: Store, path: Path) -> Optional[str]:
    try:
        parts = path.relative_to(store.root).parts
    except ValueError:
        return None
    return parts[0] if parts else None


def _problem(kind: str, message: str, ticket: Optional[str] = None,
             path: Optional[str] = None, fixable: bool = False) -> dict:
    return {"kind": kind, "message": message, "ticket": ticket, "path": path, "fixable": fixable}


def _doctor_scan(store: Store) -> List[dict]:
    problems: List[dict] = []
    items, errors = store.load_all_lenient()
    for message in errors:
        problems.append(_problem("unreadable", message))
    for stray in store.stray_files():
        problems.append(_problem("stray", "not a ticket file; move or delete it",
                                 path=_rel(store, stray)))
    by_id: Dict[str, Ticket] = {}
    origins: Dict[str, List[str]] = {}
    for path, ticket in items:
        rel = _rel(store, path)
        if ticket.id in by_id:
            problems.append(_problem("duplicate-id", f"{ticket.id} appears in more than one file",
                                     ticket=ticket.id, path=rel))
        else:
            by_id[ticket.id] = ticket
        if path.stem != ticket.id:
            problems.append(_problem("filename", f"the file is named {path.stem}.md but the id is {ticket.id}",
                                     ticket=ticket.id, path=rel))
        folder = _folder_status(store, path)
        if folder and folder != ticket.status:
            problems.append(_problem(
                "drift", f"the front matter says {ticket.status} but the file sits in {folder}/",
                ticket=ticket.id, path=rel, fixable=True))
        if ticket.status == "closed" and ticket.closed and path.parent.name != ticket.closed[:7]:
            problems.append(_problem(
                "closed-month", f"closed {ticket.closed[:7]} but filed under {path.parent.name}",
                ticket=ticket.id, path=rel, fixable=True))
        if not ticket.updated:
            problems.append(_problem("no-updated", "no updated timestamp",
                                     ticket=ticket.id, path=rel, fixable=True))
        for message in validate(ticket):
            problems.append(_problem("invalid", message, ticket=ticket.id, path=rel))
        if ticket.origin:
            origins.setdefault(ticket.origin, []).append(ticket.id)
    for ticket in by_id.values():
        for dep in ticket.depends_on:
            if dep == ticket.id:
                problems.append(_problem("self-dependency", "the ticket depends on itself", ticket=ticket.id))
            elif dep not in by_id:
                problems.append(_problem("dangling-dependency", f"depends on {dep}, which does not exist",
                                         ticket=ticket.id))
    for cycle in find_cycles(by_id):
        problems.append(_problem("cycle", "dependency cycle: " + " -> ".join(cycle + [cycle[0]]),
                                 ticket=cycle[0]))
    for origin, ids in sorted(origins.items()):
        if len(ids) > 1:
            problems.append(_problem("duplicate-origin", f"origin {origin} is used by {', '.join(sorted(ids))}",
                                     ticket=sorted(ids)[0]))
    live = [t for t in by_id.values() if t.status in ("in_progress", "review")]
    seen_pairs = set()
    for ticket in live:
        for other, pairs in scope.overlapping_tickets(ticket, live):
            key = tuple(sorted([ticket.id, other.id]))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            shown = ", ".join(f"{a} ~ {b}" for a, b in pairs[:3])
            problems.append(_problem(
                "live-overlap",
                f"{ticket.id} ({ticket.assignee or 'unassigned'}) and {other.id} "
                f"({other.assignee or 'unassigned'}) both hold overlapping scope: {shown}",
                ticket=ticket.id))
    return problems


def _doctor_fix(store: Store) -> List[str]:
    fixed: List[str] = []
    items, _ = store.load_all_lenient()
    for path, ticket in items:
        changed = False
        folder = _folder_status(store, path)
        if folder and folder != ticket.status and folder in STATUSES:
            fixed.append(f"{ticket.id}: status set to {folder} to match the folder")
            ticket.status = folder
            changed = True
        if not ticket.updated:
            ticket.updated = ticket.created or now()
            fixed.append(f"{ticket.id}: updated set from created")
            changed = True
        if changed:
            save_ticket(ticket, path)
        if ticket.status == "closed" and ticket.closed and path.parent.name != ticket.closed[:7]:
            move_ticket(path, ticket, store.root, "closed")
            fixed.append(f"{ticket.id}: re-filed into closed/{ticket.closed[:7]}")
    return fixed


def cmd_doctor(args, ctx: Context) -> Optional[int]:
    store = ctx.store
    fixed: List[str] = []
    if args.fix:
        fixed = _doctor_fix(store)
        for line in fixed:
            store.log_event("doctor", None, None, None, None, line)
    problems = _doctor_scan(store)
    if ctx.json_output:
        ctx.emit_json({"problems": problems, "fixed": fixed, "ok": not problems})
    else:
        ctx.out(views.doctor_report(problems, fixed))
    if problems:
        fixable = sum(1 for p in problems if p["fixable"])
        raise Problems(
            f"{len(problems)} problem(s) found"
            + (f"; {fixable} can be repaired with `rungs doctor --fix`" if fixable and not args.fix else "")
        )
    return None

# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def register(subparsers, ctx: Context) -> None:
    if "verify" in getattr(subparsers, "choices", {}):
        # Already registered by commands.register()'s temporary bridge, so
        # being listed in cli._command_modules() as well is harmless.
        return

    p = ctx.command(subparsers, "verify", cmd_verify,
                    "check a change set against a ticket's scope",
                    description="By default the files the executor declared at submit. --tree, "
                                "--staged and --since add what git reports, which may include "
                                "another agent's concurrent edits. Exit 3 when anything is outside.")
    ctx.add_json_option(p)
    p.add_argument("id")
    p.add_argument("--tree", action="store_true",
                   help="add the working tree's changed and untracked files")
    p.add_argument("--staged", action="store_true", help="add the index")
    p.add_argument("--since", metavar="REF", help="add the commits after REF")
    p.add_argument("--files", help="check this comma separated list instead")

    p = ctx.command(subparsers, "doctor", cmd_doctor,
                    "check the store and repair what is unambiguous",
                    description="Finds unreadable files, strays, folder/status drift, duplicate "
                                "ids, invalid values, dangling and circular dependencies, closed "
                                "tickets in the wrong month, overlapping live scopes and duplicate "
                                "origins. Exit 3 when problems remain.")
    ctx.add_json_option(p)
    p.add_argument("--fix", action="store_true",
                   help="repair the unambiguous ones: status to match the folder, closed tickets "
                        "re-filed by month, a missing updated time")
