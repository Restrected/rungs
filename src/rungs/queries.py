"""The reading commands: what is on the board, who holds it, and what to do next.

Everything here only reads the store — with one exception the design asks for:
`next --claim` takes the tickets it offers. It does that through
:func:`rungs.commands.claim_one`, so the claim checks (registered agent, the
ticket is open, skill, dependencies, scope overlap, then the exclusive create)
live in exactly one place. The import is one-directional: queries imports
commands, never the other way round.
"""

from __future__ import annotations

import argparse
from typing import Dict, List, Optional, Sequence

from . import views
from .cli import Context, NoMatch, Refused, RungsError
from .commands import HELD_STATUSES, claim_one, unmet_dependencies
from .ticket import KINDS, SKILLS, STATUSES, Ticket, TicketError, normalise_skill

__all__ = ["register"]


def _count(value: str) -> int:
    """A positive count: `--count 0` would otherwise quietly mean one."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number")
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, not {number}")
    return number


def _filtered(ctx: Context, args) -> List[Ticket]:
    tickets = [t for _, t in ctx.store.load_all()]
    if getattr(args, "status", None):
        tickets = [t for t in tickets if t.status == args.status]
    elif not getattr(args, "all", False):
        tickets = [t for t in tickets if t.status != "closed"]
    if getattr(args, "skill", None):
        wanted = normalise_skill(args.skill)
        tickets = [t for t in tickets if t.skill == wanted]
    for field in ("kind", "domain", "epic", "plan", "assignee"):
        value = getattr(args, field, None)
        if value:
            tickets = [t for t in tickets if getattr(t, field) == value]
    if getattr(args, "tag", None):
        tickets = [t for t in tickets if args.tag in t.tags]
    tickets.sort(key=lambda t: (STATUSES.index(t.status), t.priority_sort_key(), t.created, t.id))
    count = getattr(args, "count", None)
    if count:
        tickets = tickets[:count]
    return tickets


def _emit_tickets(ctx: Context, tickets: Sequence[Ticket], empty_message: str) -> None:
    if ctx.json_output:
        ctx.emit_json([t.to_dict() for t in tickets])
    elif tickets:
        ctx.out(views.ticket_table(tickets))
    if not tickets:
        raise NoMatch(empty_message)


def cmd_list(args, ctx: Context) -> Optional[int]:
    _emit_tickets(ctx, _filtered(ctx, args), "no tickets match those filters")
    return None


def cmd_triage(args, ctx: Context) -> Optional[int]:
    tickets = [t for _, t in ctx.store.load_all() if t.status == "triage"]
    tickets.sort(key=lambda t: (t.priority_sort_key(), t.created))
    _emit_tickets(ctx, tickets, "nothing is waiting in triage")
    return None


def cmd_search(args, ctx: Context) -> Optional[int]:
    needle = args.text.lower()
    found = []
    for _, ticket in ctx.store.load_all():
        haystack = " ".join([ticket.title, " ".join(ticket.tags), ticket.body]).lower()
        if needle in haystack:
            found.append(ticket)
    found.sort(key=lambda t: (STATUSES.index(t.status), t.id))
    _emit_tickets(ctx, found, f"nothing matches {args.text!r}")
    return None


def cmd_board(args, ctx: Context) -> Optional[int]:
    tickets = [t for _, t in ctx.store.load_all() if t.status != "closed"]
    if args.epic:
        tickets = [t for t in tickets if t.epic == args.epic]
    tickets.sort(key=lambda t: (t.priority_sort_key(), t.created, t.id))
    if ctx.json_output:
        grouped = {name: [t.to_dict() for t in tickets if t.status == name] for name in views.BOARD_ORDER}
        ctx.emit_json(grouped)
        return None
    ctx.out(views.board(tickets))
    return None


def cmd_show(args, ctx: Context) -> Optional[int]:
    path, ticket = ctx.store.find(args.id)
    if ctx.json_output:
        ctx.emit_json(ticket.to_dict(path))
    else:
        ctx.out(path.read_text(encoding="utf-8").rstrip("\n"))
    return None


def cmd_deps(args, ctx: Context) -> Optional[int]:
    path, ticket = ctx.store.find(args.id)
    by_id = ctx.store.by_id()
    depends = [by_id[d] for d in ticket.depends_on if d in by_id]
    missing = [d for d in ticket.depends_on if d not in by_id]
    blocks = sorted((t for t in by_id.values() if ticket.id in t.depends_on), key=lambda t: t.id)
    if ctx.json_output:
        ctx.emit_json({
            "ticket": ticket.id,
            "status": ticket.status,
            "depends_on": [t.to_dict() for t in depends],
            "missing": missing,
            "blocks": [t.to_dict() for t in blocks],
            "workable": not unmet_dependencies(ticket, by_id),
        })
        return None
    ctx.out(views.deps_report(ticket, depends, missing, blocks))
    return None


def cmd_log(args, ctx: Context) -> Optional[int]:
    ticket_id = None
    if args.id:
        _, ticket = ctx.store.find(args.id)
        ticket_id = ticket.id
    events = ctx.store.read_events(ticket_id, args.since, args.count)
    if ctx.json_output:
        ctx.emit_json([e.to_dict() for e in events])
    else:
        for line in views.events_lines(events):
            ctx.out(line)
    if not events:
        raise NoMatch("no events match")
    return None


def cmd_stale(args, ctx: Context) -> Optional[int]:
    hours = args.hours if args.hours is not None else ctx.roster.policy.stale_after_hours
    rows = ctx.store.stale(hours)
    if ctx.json_output:
        ctx.emit_json([
            {"ticket": t.to_dict(), "last_activity": stamp, "idle_hours": round(idle, 2)}
            for t, stamp, idle in rows
        ])
    elif rows:
        ctx.out(views.stale_table(rows))
    if not rows:
        raise NoMatch(f"no in_progress or review ticket has been idle for {hours} hours")
    ctx.info("a stale claim is never released automatically; a director decides (release --force)")
    return None


def cmd_agents(args, ctx: Context) -> Optional[int]:
    roster = ctx.roster
    held: Dict[str, List[Ticket]] = {}
    store = ctx.store_or_none()
    if store is not None:
        for _, ticket in store.load_all():
            if ticket.assignee and ticket.status in HELD_STATUSES:
                held.setdefault(ticket.assignee, []).append(ticket)
    agents = roster.by_skill()
    if ctx.json_output:
        ctx.emit_json({
            "project": roster.project,
            "policy": roster.policy.to_dict(),
            "agents": [
                dict(a.to_dict(), current=[t.id for t in held.get(a.id, [])]) for a in agents
            ],
        })
        return None
    if not agents:
        raise NoMatch("no agents are registered; add them to rungs.yaml")
    ctx.out(views.agents_table(agents, held))
    return None


def cmd_whoami(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    store = ctx.store
    workable, below = store.next_for(agent, count=100000, ignore_floor=True)
    held = [t for _, t in store.load_all() if t.assignee == agent.id and t.status in HELD_STATUSES]
    data = dict(agent.to_dict())
    data.update({
        "can_review": agent.can_review,
        "is_director": agent.is_director,
        "workable": len(workable),
        "below_floor": below,
        "holding": [t.id for t in held],
    })
    if ctx.json_output:
        ctx.emit_json(data)
        return None
    ctx.out(f"{agent.id}")
    ctx.out(f"  skill:   {agent.skill} (rank {agent.rank} of {len(SKILLS)})")
    ctx.out(f"  role:    {agent.role}")
    ctx.out(f"  floor:   {agent.floor or 'none (every level at or below yours)'}")
    ctx.out(f"  review:  {'yes' if agent.can_review else 'no'}")
    ctx.out(f"  claimable now: {len(workable)}"
            + (f" ({below} of them below your floor)" if below else ""))
    ctx.out(f"  holding: {', '.join(t.id for t in held) or 'nothing'}")
    return None


def cmd_next(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    store = ctx.store
    tickets, below = store.next_for(agent, args.count, args.epic, args.domain, args.ignore_floor)
    claimed: List[Ticket] = []
    if args.claim and tickets:
        for candidate in list(tickets):
            try:
                _, taken = claim_one(ctx, agent, candidate.id)
                claimed.append(taken)
            except (Refused, RungsError, TicketError) as exc:
                ctx.warn(f"{candidate.id} not claimed: {exc}")
        tickets = claimed or tickets
    if ctx.json_output:
        ctx.emit_json({
            "tickets": [t.to_dict() for t in tickets],
            "claimed": len(claimed),
            "below_floor": below,
        })
    elif tickets:
        ctx.out(views.ticket_table(tickets))
    if not tickets:
        if below and not args.ignore_floor:
            ctx.warn(f"{below} workable tickets sit below your floor; "
                     "pass --ignore-floor to see them")
        raise NoMatch(f"no workable ticket for {agent.id} at {agent.skill} or below")
    if args.claim:
        ctx.info(f"claimed {len(claimed)} of {len(tickets)} ticket(s)")
    return None


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def register(subparsers, ctx: Context) -> None:
    if "list" in getattr(subparsers, "choices", {}):
        # Already registered by commands.register()'s temporary bridge, so
        # being listed in cli._command_modules() as well is harmless.
        return
    add_agent = ctx.add_agent_option
    add_json = ctx.add_json_option

    # -- reading ----------------------------------------------------------
    p = ctx.command(subparsers, "list", cmd_list, "list tickets",
                    description="List tickets. Filters are combined with AND. Closed tickets "
                                "are left out unless --status closed or --all.")
    add_json(p)
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--skill", help=f"one of {', '.join(SKILLS)} (aliases accepted)")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--domain")
    p.add_argument("--epic")
    p.add_argument("--plan", help="the plan slug the ticket was cut from")
    p.add_argument("--assignee")
    p.add_argument("--tag")
    p.add_argument("--count", type=int, help="show at most this many")
    p.add_argument("--all", action="store_true", help="include closed tickets")

    p = ctx.command(subparsers, "next", cmd_next, "what this agent should work on next",
                    description="Workable tickets at or below the agent's level: its own level "
                                "first, then lower ones, and within a level by priority then age. "
                                "Tickets below the agent's floor are left out unless --ignore-floor.")
    add_agent(p)
    add_json(p)
    p.add_argument("--count", type=_count, default=1,
                   help="how many to offer, 1 or more (default 1)")
    p.add_argument("--claim", action="store_true", help="claim each one that is offered")
    p.add_argument("--epic", help="only this epic")
    p.add_argument("--domain", help="only this domain")
    p.add_argument("--ignore-floor", dest="ignore_floor", action="store_true",
                   help="include tickets below the agent's floor")

    p = ctx.command(subparsers, "board", cmd_board, "every open ticket grouped by status")
    add_json(p)
    p.add_argument("--epic", help="only this epic")

    p = ctx.command(subparsers, "show", cmd_show, "print one ticket file")
    add_json(p)
    p.add_argument("id", help="ticket id (a unique part of it is enough)")

    p = ctx.command(subparsers, "search", cmd_search, "search titles, tags and bodies")
    add_json(p)
    p.add_argument("text", help="case-insensitive text to look for")

    p = ctx.command(subparsers, "deps", cmd_deps, "what a ticket waits on and what waits on it")
    add_json(p)
    p.add_argument("id")

    p = ctx.command(subparsers, "log", cmd_log, "the event log",
                    description="Every state change, oldest first: when, who, what, and the move.")
    add_json(p)
    p.add_argument("id", nargs="?", help="only this ticket")
    p.add_argument("--since", help="only events at or after this timestamp")
    p.add_argument("--count", type=int, help="only the last N events")

    p = ctx.command(subparsers, "stale", cmd_stale, "claims with no activity for a while",
                    description="in_progress and review tickets with no event and no update for "
                                "N hours. Nothing is released automatically; a director decides.")
    add_json(p)
    p.add_argument("--hours", type=int, help="threshold (default: policy.stale_after_hours)")

    p = ctx.command(subparsers, "triage", cmd_triage, "tickets waiting to be made ready")
    add_json(p)

    p = ctx.command(subparsers, "agents", cmd_agents, "the roster and what each agent holds")
    add_json(p)

    p = ctx.command(subparsers, "whoami", cmd_whoami, "this agent's level, role and workload")
    add_agent(p)
    add_json(p)
