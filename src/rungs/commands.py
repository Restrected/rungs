"""The ticket lifecycle: create, and every command that moves a ticket.

Every state-changing command resolves its acting agent through the roster
(`ctx.acting_agent`), records an event in `events.jsonl` and appends an
attributed note to the ticket, so a ticket file always says who did what and
when. Policy refusals raise :class:`Refused` (exit 4).

The reading commands live in :mod:`rungs.queries` and the two checks in
:mod:`rungs.checks`; both import from here and never the other way round.
:func:`claim_one` is the one place the claim checks live, because
`queries.cmd_next --claim` runs exactly the same ones as `claim`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import scope
from .cli import Context, Refused, RungsError
from .roster import Agent, RosterError, validate_agent_id
from .store import Store
from .ticket import (
    KINDS, SECTION_KEYS, SETTABLE_FIELDS, SETTABLE_SECTIONS, SKILLS, STATUSES,
    Ticket, TicketError, clean_text, find_cycles, normalise_skill, now, one_line,
    split_sections, validate, write_atomic,
)

__all__ = [
    "register", "update_agent_file", "claim_one", "unmet_dependencies",
    "GENERATED_MARKER", "HELD_STATUSES",
]

GENERATED_MARKER = "<!-- end of generated header -->"
CURRENT_WORK_HEADING = "## Current work"
HELD_STATUSES = ("in_progress", "review")

# What `set` says when it is asked for a field a lifecycle command owns.
FIELD_OWNER = {
    "status": "use claim, release, submit, review, close or block",
    "assignee": "use claim, release or reassign",
    "reviewer": "use review",
    "verdict": "use review, or reopen to clear it",
    "review_rounds": "use review",
    "depends_on": "use depend",
    "blocked_by": "use block or unblock",
    "files": "use submit",
    "submitted": "use submit",
    "closed": "use close",
    "id": "a ticket id never changes",
    "created": "set once by create",
    "updated": "set by every lifecycle command",
    "plan": "set by cut",
    "origin": "set by intake or import-arbite",
    "evidence": "use submit",
    "review": "use review",
    "notes": "use note",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _split_list(value: Optional[str]) -> Optional[List[str]]:
    """A comma separated option value as a list; None stays None."""
    if value is None:
        return None
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return _split_list(str(value)) or []


def _priority(value) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "none", "null"):
        return None
    try:
        return int(text)
    except ValueError:
        raise RungsError(f"priority must be an integer or 'none', not {value!r}")


def _rel(store: Store, path: Path) -> str:
    try:
        return str(path.relative_to(store.project_root))
    except ValueError:
        return str(path)


def _fence_for(text: str) -> str:
    """A code fence longer than any backtick run inside the text, so evidence
    pasted from a terminal can never close the block early."""
    longest = 0
    for match in re.finditer(r"^\s*(`+)", text, re.MULTILINE):
        longest = max(longest, len(match.group(1)))
    return "`" * max(3, longest + 1)


def _read_file(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RungsError(f"cannot read the {what} {path}: {exc}")


def _sections_from_body(text: str) -> Dict[str, str]:
    """Map a markdown body's `## ` headings onto the section keys."""
    by_heading = {heading.lower(): key for key, heading in SECTION_KEYS.items()}
    found: Dict[str, str] = {}
    for heading, body in split_sections(text).items():
        if not heading:
            continue
        key = by_heading.get(heading.strip().lower())
        if key and body.strip():
            found[key] = body
    return found


# --------------------------------------------------------------------------
# Agent files
# --------------------------------------------------------------------------


def _strip_current_work(text: str) -> str:
    """Remove an existing `## Current work` section (heading to the next
    `## ` heading or the end)."""
    lines = text.splitlines()
    out: List[str] = []
    skipping = False
    for line in lines:
        if line.strip().lower() == CURRENT_WORK_HEADING.lower():
            skipping = True
            continue
        if skipping:
            if line.startswith("## "):
                skipping = False
            else:
                continue
        out.append(line)
    return "\n".join(out)


def update_agent_file(store: Store, agent_id: str) -> Path:
    """Rewrite the `## Current work` section of `.rungs/agents/<id>.md`.

    Everything above the generated-header marker belongs to the docs module
    and is preserved untouched; the section is rewritten below it.
    """
    held = [t for _, t in store.load_all()
            if t.assignee == agent_id and t.status in HELD_STATUSES]
    held.sort(key=lambda t: (STATUSES.index(t.status), t.id))
    body = [CURRENT_WORK_HEADING, ""]
    if held:
        body.extend(f"- {t.id} ({t.status}): {one_line(t.title, 120)}" for t in held)
    else:
        body.append("- none")
    section = "\n".join(body) + "\n"

    path = store.agent_file(agent_id)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        marker_at = None
        for index, line in enumerate(lines):
            if line.strip() == GENERATED_MARKER:
                marker_at = index
                break
        if marker_at is None:
            head, tail = text, ""
        else:
            head = "\n".join(lines[: marker_at + 1])
            tail = "\n".join(lines[marker_at + 1 :])
        head = _strip_current_work(head) if marker_at is None else head
        tail = _strip_current_work(tail)
        new = head.rstrip("\n") + "\n\n"
        if tail.strip():
            new += tail.strip("\n") + "\n\n"
        new += section
    else:
        new = f"# {agent_id}\n\n{section}"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(new, path)
    return path


def _refresh_agent_files(store: Store, agent_ids: Iterable[Optional[str]]) -> None:
    for agent_id in {a for a in agent_ids if a}:
        try:
            update_agent_file(store, agent_id)
        except (OSError, TicketError, RosterError):
            # An agent file is a convenience; never fail a transition for it.
            pass


# --------------------------------------------------------------------------
# Shared checks
# --------------------------------------------------------------------------


def _overlap_message(ticket: Ticket, clashes: Sequence[Tuple[Ticket, List[Tuple[str, str]]]]) -> str:
    parts = []
    for other, pairs in clashes:
        shown = ", ".join(f"{a} ~ {b}" for a, b in pairs[:4])
        if len(pairs) > 4:
            shown += ", …"
        parts.append(f"{other.id} ({other.status}, {other.assignee or 'unassigned'}): {shown}")
    return f"{ticket.id} scope overlaps live work — " + "; ".join(parts)


def _enforce_overlap(ctx: Context, ticket: Ticket, others: Iterable[Ticket],
                     allow: bool = False, hint: str = "") -> bool:
    """Apply policy.scope_overlap. Returns True when the check passed."""
    policy = "allow" if allow else ctx.roster.policy.scope_overlap
    if policy == "allow" or not ticket.scope:
        return True
    clashes = scope.overlapping_tickets(ticket, others)
    if not clashes:
        return True
    message = _overlap_message(ticket, clashes)
    if policy == "refuse":
        raise Refused(message + (f"; {hint}" if hint else ""))
    ctx.warn(message + " (policy: warn)")
    return True


def unmet_dependencies(ticket: Ticket, by_id: Dict[str, Ticket]) -> List[str]:
    return [dep for dep in ticket.depends_on
            if dep not in by_id or by_id[dep].status != "closed"]


def _require_status(ticket: Ticket, wanted: Sequence[str], action: str) -> None:
    if ticket.status not in wanted:
        raise RungsError(
            f"{ticket.id} is {ticket.status}; {action} needs it to be "
            + " or ".join(wanted)
        )


def _find(ctx: Context, term: str, unique: bool = True) -> Tuple[Path, Ticket]:
    return ctx.store.find(term, unique=unique)


def _done(ctx: Context, ticket: Ticket, path: Path, message: str) -> None:
    if ctx.json_output:
        ctx.emit_json(ticket.to_dict(path))
    else:
        ctx.out(message)


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------

CREATE_JSON_KEYS = [
    "title", "kind", "skill", "domain", "epic", "priority", "tags", "scope", "depends_on",
    "goal", "decisions", "constraints", "acceptance", "evidence_required",
]


def cmd_create(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    store = ctx.store
    spec: Dict[str, object] = {}
    sections: Dict[str, str] = {}

    if args.from_json:
        try:
            data = json.loads(_read_file(args.from_json, "json file"))
        except json.JSONDecodeError as exc:
            raise RungsError(f"{args.from_json} is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise RungsError(f"{args.from_json} must hold a JSON object")
        unknown = sorted(k for k in data if k not in CREATE_JSON_KEYS)
        if unknown:
            raise RungsError(
                f"{args.from_json}: unknown keys {', '.join(unknown)}; "
                f"accepted: {', '.join(CREATE_JSON_KEYS)}"
            )
        for key, value in data.items():
            if key in SETTABLE_SECTIONS and value is not None:
                sections[key] = str(value)
            elif value is not None:
                spec[key] = value

    if args.body_file:
        sections.update(_sections_from_body(_read_file(args.body_file, "body file")))

    for key in ("title", "kind", "skill", "domain", "epic"):
        value = getattr(args, key, None)
        if value is not None:
            spec[key] = value
    if args.priority is not None:
        spec["priority"] = args.priority
    for key in ("tags", "scope"):
        if getattr(args, key) is not None:
            spec[key] = getattr(args, key)
    if args.depends_on is not None:
        spec["depends_on"] = args.depends_on
    for key in SETTABLE_SECTIONS:
        value = getattr(args, key, None)
        if value is not None:
            sections[key] = value

    missing = [k for k in ("title", "kind", "skill") if not spec.get(k)]
    if missing:
        raise RungsError(
            "create needs " + ", ".join("--" + m for m in missing)
            + " (or the same keys in --from-json)"
        )

    kind = str(spec["kind"]).strip()
    if kind not in KINDS:
        raise RungsError(f"kind {kind!r} is not one of {', '.join(KINDS)}")
    try:
        skill = normalise_skill(spec["skill"])
    except TicketError as exc:
        raise RungsError(str(exc))

    depends_on = _as_list(spec.get("depends_on"))
    if depends_on:
        known = store.existing_ids()
        unknown = [d for d in depends_on if d not in known]
        if unknown:
            raise RungsError(f"depends_on names tickets that do not exist: {', '.join(unknown)}")

    status = "triage" if args.triage else "open"
    ticket = Ticket(
        id=store.new_id(),
        title=one_line(str(spec["title"]), 200),
        status=status,
        kind=kind,
        skill=skill,
        domain=one_line(str(spec["domain"])) if spec.get("domain") else None,
        epic=one_line(str(spec["epic"])) if spec.get("epic") else None,
        priority=_priority(spec.get("priority")),
        tags=[one_line(t) for t in _as_list(spec.get("tags"))],
        scope=[scope.normalise(s) for s in _as_list(spec.get("scope"))],
        depends_on=depends_on,
        created=now(),
    )
    ticket.updated = ticket.created
    for key in SETTABLE_SECTIONS:
        ticket.set_section(key, sections.get(key, ""))
    problems = validate(ticket)
    if problems:
        raise RungsError("the ticket is not valid: " + "; ".join(problems))
    path = store.create(ticket)
    store.log_event("create", ticket.id, agent.id, None, status, ticket.kind)
    if ctx.json_output:
        ctx.emit_json(ticket.to_dict(path))
    else:
        ctx.out(ticket.id)
        ctx.info(f"created {ticket.id} in {status}: {ticket.title}")
    return None


# --------------------------------------------------------------------------
# Claiming (shared with queries.cmd_next)
# --------------------------------------------------------------------------


def claim_one(ctx: Context, agent: Agent, term: str, force: bool = False,
               allow_overlap: bool = False) -> Tuple[Path, Ticket]:
    """Every claim check in the order of DESIGN.md section 5, then the
    exclusive create that settles a race."""
    store = ctx.store
    path, ticket = store.find(term, unique=True)
    if ticket.status != "open":
        hint = ""
        if ticket.status == "triage":
            hint = "; run `rungs ready` first"
        elif ticket.status in HELD_STATUSES and ticket.assignee:
            hint = f"; {ticket.assignee} holds it"
        elif ticket.status == "blocked":
            hint = "; run `rungs unblock` first"
        elif ticket.status == "shelved":
            hint = "; run `rungs unshelve` first"
        raise Refused(f"{ticket.id} is {ticket.status}, not open{hint}")
    forced_skill = False
    if ticket.skill_rank() > agent.rank:
        if not force:
            raise Refused(
                f"{ticket.id} is a {ticket.skill} ticket and {agent.id} is {agent.skill}; "
                "a higher-level agent should take it (--force claims it anyway)"
            )
        forced_skill = True
    by_id = store.by_id()
    unmet = unmet_dependencies(ticket, by_id)
    if unmet:
        detail = ", ".join(
            f"{dep} ({by_id[dep].status})" if dep in by_id else f"{dep} (missing)" for dep in unmet
        )
        raise Refused(f"{ticket.id} waits on {detail}")
    _enforce_overlap(ctx, ticket, by_id.values(), allow=allow_overlap,
                     hint="finish or release the other ticket, or claim with --allow-overlap")
    ticket.append_note(agent.id, "claimed")
    if forced_skill:
        ticket.append_note(
            agent.id,
            f"claimed above skill level: the ticket is {ticket.skill}, the agent is {agent.skill} (--force)",
        )
    new_path = store.claim(path, ticket, agent.id)
    store.log_event("claim", ticket.id, agent.id, "open", "in_progress",
                    "force" if forced_skill else None)
    _refresh_agent_files(store, [agent.id])
    return new_path, ticket


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def cmd_ready(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["triage"], "ready")
    missing = []
    if not (ticket.title or "").strip():
        missing.append("title")
    if not ticket.section("goal").strip():
        missing.append("the Goal section")
    if not (ticket.kind or "").strip():
        missing.append("kind")
    if not (ticket.skill or "").strip():
        missing.append("skill")
    if not ticket.section("acceptance").strip():
        missing.append("the Acceptance criteria section")
    if missing:
        raise RungsError(
            f"{ticket.id} is not ready: {', '.join(missing)} still empty. "
            f"Fill them in with `rungs set {ticket.id} ...`, then run ready again."
        )
    # A triage ticket can come from anywhere — intake, import-arbite, a hand
    # edit — so its fields are checked here rather than trusted.
    problems = validate(ticket)
    for entry in ticket.scope:
        try:
            scope.normalise(entry)
        except scope.ScopeError as exc:
            problems.append(str(exc))
    if problems:
        raise RungsError(
            f"{ticket.id} is not ready: " + "; ".join(problems)
            + f". Fix it with `rungs set {ticket.id} ...`, then run ready again."
        )
    ticket.append_note(agent.id, "made ready for work")
    new_path = ctx.store.move(path, ticket, "open")
    ctx.store.log_event("ready", ticket.id, agent.id, "triage", "open", None)
    _done(ctx, ticket, new_path, f"{ticket.id} is open and can be claimed")
    return None


def cmd_claim(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = claim_one(ctx, agent, args.id, force=args.force,
                              allow_overlap=args.allow_overlap)
    _done(ctx, ticket, path, f"{ticket.id} claimed by {agent.id}: {ticket.title}")
    return None


def cmd_release(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["in_progress", "review", "blocked"], "release")
    holder = ticket.assignee
    if holder != agent.id:
        if not (agent.is_director and args.force):
            raise Refused(
                f"{ticket.id} is held by {holder or 'nobody'}; only the assignee releases it "
                "(a director may take it back with --force)"
            )
    reason = one_line(args.reason) if args.reason else "no reason given"
    note = f"released to open: {reason}"
    if holder != agent.id:
        note = f"released {holder}'s claim (director --force): {reason}"
    ticket.append_note(agent.id, note)
    ticket.assignee = None
    from_status = ticket.status
    new_path = ctx.store.move(path, ticket, "open")
    ctx.store.log_event("release", ticket.id, agent.id, from_status, "open", reason)
    _refresh_agent_files(ctx.store, [agent.id, holder])
    _done(ctx, ticket, new_path, f"{ticket.id} is open again")
    return None


def cmd_reassign(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    if not agent.is_director:
        raise Refused(f"only a director may reassign; {agent.id} has the role {agent.role}")
    target = ctx.roster.agent(validate_agent_id(args.to))
    path, ticket = _find(ctx, args.id)
    if ticket.skill_rank() > target.rank:
        raise Refused(
            f"{ticket.id} is a {ticket.skill} ticket and {target.id} is {target.skill}; "
            "reassign it to an agent that can reach it"
        )
    previous = ticket.assignee
    _enforce_overlap(ctx, ticket, ctx.store.by_id().values(),
                     hint="the other ticket must finish or be released first")
    reason = one_line(args.reason) if args.reason else "no reason given"
    ticket.assignee = target.id
    ticket.append_note(agent.id, f"reassigned from {previous or 'nobody'} to {target.id}: {reason}")
    ctx.store.save(path, ticket)
    ctx.store.log_event("reassign", ticket.id, agent.id, ticket.status, ticket.status,
                        f"{previous or 'nobody'} -> {target.id}")
    _refresh_agent_files(ctx.store, [previous, target.id])
    _done(ctx, ticket, path, f"{ticket.id} now belongs to {target.id} ({ticket.status})")
    return None


def cmd_note(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    message = " ".join(args.message).strip()
    if not message:
        raise RungsError("a note needs a message")
    ticket.append_note(agent.id, message)
    ticket.updated = now()
    ctx.store.save(path, ticket)
    ctx.store.log_event("note", ticket.id, agent.id, ticket.status, ticket.status,
                        one_line(message, 120))
    _done(ctx, ticket, path, f"note added to {ticket.id}")
    return None


def cmd_block(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["open", "in_progress", "review"], "block")
    if ticket.status in HELD_STATUSES and ticket.assignee != agent.id and not agent.is_director:
        raise Refused(f"{ticket.id} is held by {ticket.assignee}; only the assignee or a director blocks it")
    reason = one_line(args.reason)
    if not reason:
        raise RungsError("block needs --reason: say what the ticket waits for")
    from_status = ticket.status
    ticket.blocked_by = reason
    ticket.append_note(agent.id, f"blocked: {reason}")
    new_path = ctx.store.move(path, ticket, "blocked")
    ctx.store.log_event("block", ticket.id, agent.id, from_status, "blocked", reason)
    _done(ctx, ticket, new_path, f"{ticket.id} is blocked: {reason}")
    return None


def cmd_unblock(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["blocked"], "unblock")
    was = ticket.blocked_by
    held_by = ticket.assignee
    reason = one_line(args.reason) if args.reason else "blocker cleared"
    target = "open"
    clash_note = None
    if ticket.assignee:
        clashes = scope.overlapping_tickets(ticket, ctx.store.by_id().values()) if ticket.scope else []
        if clashes and ctx.roster.policy.scope_overlap == "refuse":
            clash_note = _overlap_message(ticket, clashes)
        else:
            if clashes:
                ctx.warn(_overlap_message(ticket, clashes) + " (policy: warn)")
            target = "in_progress"
    ticket.blocked_by = None
    ticket.append_note(agent.id, f"unblocked ({was or 'no reason recorded'}): {reason}")
    if target == "open" and ticket.assignee:
        ticket.append_note(
            agent.id,
            f"assignee {ticket.assignee} cleared and the ticket sent to open: {clash_note}"
            if clash_note else f"assignee {ticket.assignee} cleared and the ticket sent to open",
        )
        ticket.assignee = None
    new_path = ctx.store.move(path, ticket, target)
    ctx.store.log_event("unblock", ticket.id, agent.id, "blocked", target,
                        "overlap" if clash_note else reason)
    _refresh_agent_files(ctx.store, [agent.id, held_by])
    _done(ctx, ticket, new_path, f"{ticket.id} is {target}")
    return None


def cmd_shelve(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["open", "triage"], "shelve")
    reason = one_line(args.reason) if args.reason else "parked"
    from_status = ticket.status
    ticket.append_note(agent.id, f"shelved: {reason}")
    new_path = ctx.store.move(path, ticket, "shelved")
    ctx.store.log_event("shelve", ticket.id, agent.id, from_status, "shelved", reason)
    _done(ctx, ticket, new_path, f"{ticket.id} is shelved")
    return None


def cmd_unshelve(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["shelved"], "unshelve")
    reason = one_line(args.reason) if args.reason else "back on the board"
    ticket.append_note(agent.id, f"unshelved: {reason}")
    new_path = ctx.store.move(path, ticket, "open")
    ctx.store.log_event("unshelve", ticket.id, agent.id, "shelved", "open", reason)
    _done(ctx, ticket, new_path, f"{ticket.id} is open")
    return None


def cmd_submit(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    if ticket.assignee != agent.id:
        raise Refused(
            f"{ticket.id} is held by {ticket.assignee or 'nobody'}; only the assignee submits it"
        )
    _require_status(ticket, ["in_progress"], "submit")
    files = _split_list(args.files) or []
    if not (args.summary or args.checks or args.evidence_file or files):
        raise RungsError(
            "nothing to submit: pass --summary, --files, --checks or --evidence-file"
        )
    if args.evidence_file:
        evidence = clean_text(_read_file(args.evidence_file, "evidence file"))
    else:
        parts: List[str] = []
        if args.summary:
            parts.append("### Summary\n" + clean_text(args.summary))
        if files:
            parts.append("### Files\n" + "\n".join(f"- {clean_text(f)}" for f in files))
        if args.checks:
            checks = clean_text(args.checks)
            fence = _fence_for(checks)
            parts.append(f"### Checks\n{fence}\n{checks}\n{fence}")
        evidence = "\n\n".join(parts)
    ticket.set_section("evidence", evidence)
    if files:
        ticket.files = [scope.normalise(f) for f in files]
    ticket.submitted = now()
    ticket.append_note(agent.id, f"submitted for review ({len(ticket.files)} file(s) declared)")
    new_path = ctx.store.move(path, ticket, "review")
    ctx.store.log_event("submit", ticket.id, agent.id, "in_progress", "review",
                        f"{len(ticket.files)} files")
    _refresh_agent_files(ctx.store, [agent.id])
    _done(ctx, ticket, new_path, f"{ticket.id} is in review; a different agent must accept it")
    return None


def cmd_review(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["review"], "review")
    if args.force and not agent.is_director:
        raise Refused(
            f"only a director may force a review; {agent.id} has the role {agent.role}. "
            "Nothing else bypasses the review gate."
        )
    if not agent.can_review:
        raise Refused(
            f"{agent.id} has the role {agent.role}; only a director or a reviewer reviews {ticket.id}"
        )
    if ticket.assignee == agent.id and not args.force:
        raise Refused(
            f"{agent.id} submitted {ticket.id}; a review must come from a different agent "
            "(a director may force its own review with --force, which is recorded)"
        )
    findings = ""
    if args.findings_file:
        findings = clean_text(_read_file(args.findings_file, "findings file"))
    elif args.findings:
        findings = clean_text(args.findings)
    assignee = ticket.assignee
    # --force is a director's alone and its only effect is to allow a
    # self-review, so it is recorded only when it actually bypassed something.
    forced = bool(args.force) and assignee == agent.id
    if forced:
        ticket.append_note(
            agent.id, f"forced review: the director {agent.id} reviewed its own submission (--force)")
    if args.verdict == "accept":
        ticket.verdict = "accepted"
        ticket.reviewer = agent.id
        ticket.closed = now()
        if findings:
            ticket.append_to_section(
                "review", f"### Round {ticket.review_rounds + 1} ({ticket.closed}, {agent.id})\n\naccepted\n\n{findings}"
            )
        ticket.append_note(agent.id, "review accepted; closed")
        new_path = ctx.store.move(path, ticket, "closed")
        ctx.store.log_event("review", ticket.id, agent.id, "review", "closed",
                            "accepted, forced" if forced else "accepted")
        _refresh_agent_files(ctx.store, [agent.id, assignee])
        _done(ctx, ticket, new_path, f"{ticket.id} accepted and closed")
        return None

    if not findings:
        raise RungsError("a returned review needs --findings or --findings-file: say what to fix")
    ticket.review_rounds += 1
    ticket.verdict = "returned"
    ticket.reviewer = agent.id
    ticket.submitted = None
    ticket.append_to_section(
        "review", f"### Round {ticket.review_rounds} ({now()}, {agent.id})\n\n{findings}"
    )
    target = "open" if args.release else "in_progress"
    if args.release:
        ticket.append_note(agent.id, f"returned with findings and released from {assignee or 'nobody'}")
        ticket.assignee = None
    else:
        ticket.append_note(agent.id, f"returned with findings (round {ticket.review_rounds})")
    new_path = ctx.store.move(path, ticket, target)
    ctx.store.log_event("review", ticket.id, agent.id, "review", target,
                        f"returned round {ticket.review_rounds}" + (", forced" if forced else ""))
    _refresh_agent_files(ctx.store, [agent.id, assignee])
    _done(ctx, ticket, new_path,
          f"{ticket.id} returned to {target}" + (" (unassigned)" if args.release else f" for {assignee}"))
    return None


def cmd_close(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    if ticket.status == "closed":
        raise RungsError(f"{ticket.id} is already closed")
    reason = one_line(args.reason) if args.reason else "no reason given"
    bypass = False
    if ctx.roster.policy.review_required and ticket.verdict != "accepted":
        if not args.no_review:
            raise Refused(
                f"{ticket.id} has no accepted review; submit it and have another agent accept it "
                "(a director may close it with --no-review)"
            )
        if not agent.is_director:
            raise Refused(f"only a director may close without a review; {agent.id} has the role {agent.role}")
        bypass = True
    from_status = ticket.status
    assignee = ticket.assignee
    ticket.closed = now()
    if bypass:
        ticket.append_note(agent.id, f"closed without review: {reason}")
    else:
        ticket.append_note(agent.id, f"closed: {reason}")
    new_path = ctx.store.move(path, ticket, "closed")
    ctx.store.log_event("close", ticket.id, agent.id, from_status, "closed",
                        "no-review" if bypass else reason)
    _refresh_agent_files(ctx.store, [agent.id, assignee])
    _done(ctx, ticket, new_path, f"{ticket.id} is closed")
    return None


def cmd_reopen(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    _require_status(ticket, ["closed"], "reopen")
    reason = one_line(args.reason) if args.reason else "no reason given"
    previous = ticket.assignee
    ticket.verdict = None
    ticket.reviewer = None
    ticket.submitted = None
    ticket.files = []
    ticket.closed = None
    ticket.assignee = None
    ticket.append_note(agent.id, f"reopened (verdict, reviewer and declared files cleared): {reason}")
    new_path = ctx.store.move(path, ticket, "open")
    ctx.store.log_event("reopen", ticket.id, agent.id, "closed", "open", reason)
    _refresh_agent_files(ctx.store, [agent.id, previous])
    _done(ctx, ticket, new_path, f"{ticket.id} is open again")
    return None


def cmd_set(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    path, ticket = _find(ctx, args.id)
    pairs = list(args.pairs)
    if not pairs or len(pairs) % 2:
        raise RungsError("set takes FIELD VALUE pairs, e.g. `rungs set rg-1a2b3 skill high priority 2`")
    changes: List[str] = []
    for index in range(0, len(pairs), 2):
        field = pairs[index].strip().lower().replace("-", "_")
        value = pairs[index + 1]
        if field in SETTABLE_SECTIONS:
            ticket.set_section(field, value)
            changes.append(f"{field} section rewritten")
            continue
        if field not in SETTABLE_FIELDS:
            hint = FIELD_OWNER.get(field)
            if hint:
                raise RungsError(f"set may not change {field}: {hint}")
            raise RungsError(
                f"{field!r} is not a ticket field. Settable: "
                f"{', '.join(SETTABLE_FIELDS)}; sections: {', '.join(SETTABLE_SECTIONS)}"
            )
        if field == "title":
            ticket.title = one_line(value, 200)
        elif field == "kind":
            if value not in KINDS:
                raise RungsError(f"kind {value!r} is not one of {', '.join(KINDS)}")
            ticket.kind = value
        elif field == "skill":
            ticket.skill = normalise_skill(value)
        elif field == "priority":
            ticket.priority = _priority(value)
        elif field in ("domain", "epic"):
            text = one_line(value)
            setattr(ticket, field, text or None)
        elif field == "tags":
            ticket.tags = [one_line(t) for t in (_split_list(value) or [])]
        elif field == "scope":
            ticket.scope = [scope.normalise(s) for s in (_split_list(value) or [])]
        changes.append(f"{field}={getattr(ticket, field)!r}")
    problems = validate(ticket)
    if problems:
        raise RungsError("that change would make the ticket invalid: " + "; ".join(problems))
    ticket.updated = now()
    ticket.append_note(agent.id, "set " + ", ".join(changes))
    ctx.store.save(path, ticket)
    ctx.store.log_event("set", ticket.id, agent.id, ticket.status, ticket.status,
                        one_line(", ".join(changes), 160))
    _done(ctx, ticket, path, f"{ticket.id} updated: {', '.join(changes)}")
    return None


def cmd_depend(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    if bool(args.on) == bool(args.clear):
        raise RungsError("depend takes either --on ID or --clear")
    path, ticket = _find(ctx, args.id)
    if args.clear:
        ticket.depends_on = []
        detail = "cleared"
    else:
        _, other = ctx.store.find(args.on, unique=True)
        if other.id == ticket.id:
            raise RungsError("a ticket cannot depend on itself")
        if other.id in ticket.depends_on:
            raise RungsError(f"{ticket.id} already depends on {other.id}")
        ticket.depends_on = ticket.depends_on + [other.id]
        by_id = ctx.store.by_id()
        by_id[ticket.id] = ticket
        cycles = [c for c in find_cycles(by_id) if ticket.id in c]
        if cycles:
            raise RungsError(
                "that dependency makes a cycle: " + " -> ".join(cycles[0] + [cycles[0][0]])
            )
        detail = f"+{other.id}"
    ticket.updated = now()
    ticket.append_note(agent.id, f"depends_on {detail}")
    ctx.store.save(path, ticket)
    ctx.store.log_event("depend", ticket.id, agent.id, ticket.status, ticket.status, detail)
    _done(ctx, ticket, path, f"{ticket.id} depends on: {', '.join(ticket.depends_on) or 'nothing'}")
    return None


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def register(subparsers, ctx: Context) -> None:
    add_agent = ctx.add_agent_option
    add_json = ctx.add_json_option

    # -- create -----------------------------------------------------------
    p = ctx.command(subparsers, "create", cmd_create, "create a ticket",
                    description="Create one ticket. --title, --kind and --skill are required "
                                "(or supplied by --from-json). The skill level is the lowest "
                                "level of agent that can work it, not its urgency.")
    add_agent(p)
    add_json(p)
    p.add_argument("--title", help="one line, what the ticket is")
    p.add_argument("--kind", choices=KINDS, help="what sort of work this is")
    p.add_argument("--skill", help=f"lowest level that can work it: {', '.join(SKILLS)}")
    p.add_argument("--domain", help="routing hint, e.g. ui, python, security")
    p.add_argument("--epic", help="grouping label")
    p.add_argument("--priority", help="integer, lower is more urgent, or 'none'")
    p.add_argument("--tags", help="comma separated search labels")
    p.add_argument("--scope", help="comma separated paths the ticket may change "
                                   "(a trailing / is a directory, * and ** are globs)")
    p.add_argument("--depends-on", dest="depends_on",
                   help="comma separated ticket ids that must close first")
    p.add_argument("--goal", help="the Goal section")
    p.add_argument("--decisions", help="the Decisions already made section")
    p.add_argument("--constraints", help="the Constraints section")
    p.add_argument("--acceptance", help="the Acceptance criteria section")
    p.add_argument("--evidence-required", dest="evidence_required", help="the Evidence required section")
    p.add_argument("--body-file", dest="body_file",
                   help="a markdown file whose '## ' headings fill the sections")
    p.add_argument("--from-json", dest="from_json",
                   help="a JSON object with the same keys as these options")
    p.add_argument("--triage", action="store_true",
                   help="create it in triage/ instead of open/")

    # -- lifecycle --------------------------------------------------------
    p = ctx.command(subparsers, "ready", cmd_ready, "triage -> open",
                    description="Move a triage ticket to open once title, Goal, kind, skill and "
                                "Acceptance criteria are filled in.")
    add_agent(p)
    add_json(p)
    p.add_argument("id")

    p = ctx.command(subparsers, "claim", cmd_claim, "take an open ticket",
                    description="Checks, in order: the agent is registered, the ticket is open, "
                                "its skill level is at or below the agent's, its dependencies are "
                                "closed, and its scope does not overlap live work.")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--force", action="store_true",
                   help="claim above the agent's skill level; a note records it")
    p.add_argument("--allow-overlap", dest="allow_overlap", action="store_true",
                   help="claim even though the scope overlaps live work")

    p = ctx.command(subparsers, "release", cmd_release, "give a ticket back to open")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--reason", help="why it is being given back")
    p.add_argument("--force", action="store_true", help="director only: release another agent's claim")

    p = ctx.command(subparsers, "reassign", cmd_reassign, "director only: move a ticket to another agent")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--to", required=True, metavar="AGENT", help="the new assignee")
    p.add_argument("--reason")

    p = ctx.command(subparsers, "note", cmd_note, "add an attributed note")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("message", nargs="+", help="the note text")

    p = ctx.command(subparsers, "block", cmd_block, "mark a ticket stalled")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--reason", required=True, help="what it waits for")

    p = ctx.command(subparsers, "unblock", cmd_unblock, "clear a block",
                    description="Back to in_progress when it still has an assignee and the scope "
                                "check passes, otherwise to open with the assignee cleared.")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--reason")

    p = ctx.command(subparsers, "shelve", cmd_shelve, "park a ticket")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--reason")

    p = ctx.command(subparsers, "unshelve", cmd_unshelve, "put a parked ticket back on the board")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--reason")

    p = ctx.command(subparsers, "submit", cmd_submit, "hand work in for review",
                    description="The assignee writes the Evidence section and declares the files "
                                "it changed; the ticket moves to review for a different agent.")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--summary", help="what was done, in plain words")
    p.add_argument("--files", help="comma separated paths that were changed")
    p.add_argument("--checks",
                   help="the commands run and their result; kept as given except that control "
                        "characters are removed, runs of three dashes are broken and markdown "
                        "headings are escaped, so pasted output cannot forge ticket structure")
    p.add_argument("--evidence-file", dest="evidence_file",
                   help="a file whose content becomes the whole Evidence section (cleaned the "
                        "same way as --checks)")

    p = ctx.command(subparsers, "review", cmd_review, "accept or return a submitted ticket")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--verdict", required=True, choices=["accept", "return"])
    p.add_argument("--findings",
                   help="what must be fixed (required when returning); cleaned the same way "
                        "as --checks")
    p.add_argument("--findings-file", dest="findings_file", help="the same, from a file")
    p.add_argument("--release", action="store_true",
                   help="return it to open and clear the assignee")
    p.add_argument("--force", action="store_true",
                   help="director only: review your own submission; recorded in a note and in "
                        "the event log. Nothing else bypasses the review gate")

    p = ctx.command(subparsers, "close", cmd_close, "close a ticket",
                    description="A ticket without an accepted review can only be closed by a "
                                "director passing --no-review; the bypass is written to the notes "
                                "and the event log.")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--no-review", dest="no_review", action="store_true",
                   help="director only: close without a review")
    p.add_argument("--reason")

    p = ctx.command(subparsers, "reopen", cmd_reopen, "reopen a closed ticket",
                    description="Clears verdict, reviewer, submitted, the declared files and the "
                                "close time, so an old acceptance never covers new work.")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--reason")

    p = ctx.command(subparsers, "set", cmd_set, "change a ticket field or section",
                    description="FIELD VALUE pairs. Fields: " + ", ".join(SETTABLE_FIELDS)
                    + ". Sections: " + ", ".join(SETTABLE_SECTIONS)
                    + ". Every other field belongs to a lifecycle command.")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("pairs", nargs="+", metavar="FIELD VALUE")

    p = ctx.command(subparsers, "depend", cmd_depend, "add or clear a dependency")
    add_agent(p)
    add_json(p)
    p.add_argument("id")
    p.add_argument("--on", metavar="ID", help="the ticket that must close first")
    p.add_argument("--clear", action="store_true", help="remove every dependency")

