"""Plan files: the grammar of DESIGN.md section 8, and `cut`.

A plan is one markdown file in `.rungs/plans/<slug>.md`. The director writes
the design once and lists the tickets under a `## Tickets` heading; `rungs cut`
validates the whole file, creates one ticket per block with the dependencies
resolved, and writes the key → id table back so the plan can grow and be cut
again. Nothing is created when the plan has any error.

The full grammar, with an example, is in docs/PLANS.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import miniyaml, scope, views
from .cli import Context, NoMatch, RungsError
from .store import Store
from .ticket import (
    ID_PATTERN, KINDS, SKILLS, Ticket, TicketError, create_exclusive, normalise_skill,
    now, one_line, validate, write_atomic,
)

__all__ = ["register", "PlanBlock", "Plan", "parse_plan", "PLAN_FIELDS", "PLAN_SECTIONS"]

PLAN_STATUSES = ["draft", "approved", "cut", "done"]
PLAN_FIELDS = ["kind", "skill", "domain", "priority", "tags", "scope", "depends_on", "epic"]
LIST_FIELDS = {"tags", "scope", "depends_on"}
REQUIRED_FIELDS = ["kind", "skill"]
# `#### ` headings, and the ticket section each one fills.
PLAN_SECTIONS = {
    "goal": "goal",
    "decisions already made": "decisions",
    "decisions": "decisions",
    "constraints": "constraints",
    "acceptance criteria": "acceptance",
    "acceptance": "acceptance",
    "evidence required": "evidence_required",
    "evidence": "evidence_required",
}
REQUIRED_SECTIONS = [("goal", "Goal"), ("acceptance", "Acceptance criteria")]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
BLOCK_RE = re.compile(r"^###\s+(?P<key>\S+)\s*(?:\s—\s|\s–\s|\s--\s|:\s)\s*(?P<title>.+?)\s*$")
BULLET_RE = re.compile(r"^\s*-\s+(?P<field>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(?P<value>.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
CUT_ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]*)\s*\|\s*([a-z]{1,8}-[0-9a-f]{4,8})\s*\|\s*$")
CUT_HEADING = "## Cut tickets"


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass
class PlanBlock:
    key: str
    title: str
    line: int
    fields: Dict[str, str] = field(default_factory=dict)
    sections: Dict[str, str] = field(default_factory=dict)

    def list_field(self, name: str) -> List[str]:
        raw = (self.fields.get(name) or "").strip()
        if raw == "" or raw.lower() == "none":
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "line": self.line,
                "fields": dict(self.fields), "sections": dict(self.sections)}


@dataclass
class Plan:
    slug: str
    title: str = ""
    status: Optional[str] = None
    owner: Optional[str] = None
    epic: Optional[str] = None
    created: Optional[str] = None
    path: Optional[Path] = None
    text: str = ""
    blocks: List[PlanBlock] = field(default_factory=list)
    cut: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def block(self, key: str) -> Optional[PlanBlock]:
        for block in self.blocks:
            if block.key == key:
                return block
        return None

    def to_dict(self) -> dict:
        return {
            "slug": self.slug, "title": self.title, "status": self.status, "owner": self.owner,
            "epic": self.epic, "created": self.created,
            "path": str(self.path) if self.path else None,
            "tickets": [b.to_dict() for b in self.blocks], "cut": dict(self.cut),
            "errors": list(self.errors),
        }


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _skip_map(lines: Sequence[str]) -> List[bool]:
    """True for every line inside a fenced code block or an HTML comment, so
    text copied verbatim (and the commented example a scaffold carries) is
    never read as grammar."""
    skip = [False] * len(lines)
    fence: Optional[str] = None
    in_comment = False
    for index, line in enumerate(lines):
        if in_comment:
            skip[index] = True
            if "-->" in line:
                in_comment = False
            continue
        if fence is None and line.lstrip().startswith("<!--") and "-->" not in line:
            skip[index] = True
            in_comment = True
            continue
        match = FENCE_RE.match(line)
        if match:
            skip[index] = True
            if fence is None:
                fence = match.group(1)
            elif fence == match.group(1):
                fence = None
            continue
        skip[index] = fence is not None
    return skip


def _split_front(text: str) -> Tuple[Dict[str, object], int, List[str]]:
    """(front matter, index of the line after it, all lines). The boundary is
    a line that is exactly '---', the same rule tickets use."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RungsError("a plan file must start with a '---' front matter line")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            try:
                data = miniyaml.loads("\n".join(lines[1:index]))
            except miniyaml.YamlError as exc:
                raise RungsError(f"plan front matter: {exc}")
            return data, index + 1, lines
    raise RungsError("the plan front matter has no closing '---' line")


def _read_cut_table(lines: Sequence[str], skip: Sequence[bool]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    inside = False
    for index, line in enumerate(lines):
        if skip[index]:
            continue
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "cut tickets"
            continue
        if inside:
            match = CUT_ROW_RE.match(line.strip())
            if match:
                mapping[match.group(1)] = match.group(2)
    return mapping


def parse_plan(text: str, path: Optional[Path] = None, slug: Optional[str] = None) -> Plan:
    """Parse a plan file. Grammar errors are collected on `plan.errors` with
    line numbers; the caller decides what to do with them."""
    front, body_start, lines = _split_front(text)
    skip = _skip_map(lines)
    plan = Plan(
        slug=str(front.get("slug") or slug or (path.stem if path else "")),
        title=str(front.get("title") or ""),
        status=str(front["status"]) if front.get("status") else None,
        owner=str(front["owner"]) if front.get("owner") else None,
        epic=str(front["epic"]) if front.get("epic") else None,
        created=str(front["created"]) if front.get("created") else None,
        path=path, text=text,
    )
    if plan.status and plan.status not in PLAN_STATUSES:
        plan.errors.append(f"line 1: status {plan.status!r} is not one of {', '.join(PLAN_STATUSES)}")
    plan.cut = _read_cut_table(lines, skip)

    start = None
    end = len(lines)
    for index in range(body_start, len(lines)):
        if skip[index] or not lines[index].startswith("## "):
            continue
        heading = lines[index][3:].strip().lower()
        if start is None and heading == "tickets":
            start = index + 1
        elif start is not None:
            end = index
            break
    if start is None:
        plan.errors.append("the plan has no '## Tickets' heading, so there is nothing to cut")
        return plan

    current: Optional[PlanBlock] = None
    section: Optional[str] = None
    buffer: List[str] = []
    seen: Dict[str, int] = {}

    def close_section() -> None:
        if current is not None and section is not None:
            body = "\n".join(buffer).strip("\n")
            if section in current.sections and current.sections[section].strip():
                current.sections[section] += "\n\n" + body
            else:
                current.sections[section] = body

    for index in range(start, end):
        raw = lines[index]
        number = index + 1
        if skip[index]:
            if current is not None and section is not None:
                buffer.append(raw)
            continue
        if raw.startswith("### "):
            close_section()
            section = None
            buffer = []
            match = BLOCK_RE.match(raw)
            if not match:
                plan.errors.append(
                    f"line {number}: expected '### <key> — <title>' "
                    "(separator: an em dash, ' -- ' or ': ')"
                )
                current = None
                continue
            key = match.group("key")
            if not KEY_RE.match(key):
                plan.errors.append(
                    f"line {number}: ticket key {key!r} must match [a-z0-9][a-z0-9-]{{0,39}}"
                )
                current = None
                continue
            if key in seen:
                plan.errors.append(f"line {number}: duplicate ticket key {key!r} (first at line {seen[key]})")
                current = None
                continue
            seen[key] = number
            current = PlanBlock(key=key, title=one_line(match.group("title"), 200), line=number)
            plan.blocks.append(current)
            continue
        if raw.startswith("#### "):
            if current is None:
                plan.errors.append(f"line {number}: a '#### ' heading before any '### <key> — <title>'")
                continue
            close_section()
            buffer = []
            name = raw[5:].strip().lower().rstrip(":")
            section = PLAN_SECTIONS.get(name)
            if section is None:
                plan.errors.append(
                    f"line {number}: unknown section {raw[5:].strip()!r}; use "
                    "Goal, Decisions already made, Constraints, Acceptance criteria or Evidence required"
                )
            continue
        if current is None:
            continue  # prose between '## Tickets' and the first block
        if section is not None:
            buffer.append(raw)
            continue
        if not raw.strip():
            continue
        bullet = BULLET_RE.match(raw)
        if not bullet:
            plan.errors.append(
                f"line {number}: expected '- field: value' or a '#### ' heading, got {raw.strip()!r}"
            )
            continue
        name = bullet.group("field").strip().lower().replace("-", "_")
        if name not in PLAN_FIELDS:
            plan.errors.append(
                f"line {number}: unknown field {bullet.group('field')!r}; use "
                + ", ".join(PLAN_FIELDS)
            )
            continue
        if name in current.fields:
            plan.errors.append(f"line {number}: field {name!r} is given twice for {current.key!r}")
            continue
        current.fields[name] = bullet.group("value").strip()
    close_section()
    return plan


def validate_plan(plan: Plan, known_ids: Optional[set] = None) -> List[str]:
    """Field, section and dependency errors, each with the block's line."""
    errors: List[str] = []
    known_ids = known_ids or set()
    keys = {block.key for block in plan.blocks}
    for block in plan.blocks:
        where = f"line {block.line} ({block.key})"
        for name in REQUIRED_FIELDS:
            if not (block.fields.get(name) or "").strip():
                errors.append(f"{where}: missing required field {name}")
        kind = (block.fields.get("kind") or "").strip()
        if kind and kind not in KINDS:
            errors.append(f"{where}: kind {kind!r} is not one of {', '.join(KINDS)}")
        skill = (block.fields.get("skill") or "").strip()
        if skill:
            try:
                normalise_skill(skill)
            except TicketError:
                errors.append(f"{where}: skill {skill!r} is not one of {', '.join(SKILLS)}")
        priority = (block.fields.get("priority") or "").strip()
        if priority and priority.lower() != "none":
            try:
                int(priority)
            except ValueError:
                errors.append(f"{where}: priority {priority!r} must be an integer or 'none'")
        for entry in block.list_field("scope"):
            try:
                scope.normalise(entry)
            except scope.ScopeError as exc:
                errors.append(f"{where}: {exc}")
        for key, heading in REQUIRED_SECTIONS:
            if not (block.sections.get(key) or "").strip():
                errors.append(f"{where}: the '#### {heading}' section is missing or empty")
        for dep in block.list_field("depends_on"):
            if dep in keys:
                if dep == block.key:
                    errors.append(f"{where}: depends on itself")
                continue
            if ID_PATTERN.match(dep):
                if dep not in known_ids:
                    errors.append(f"{where}: depends_on {dep} is not a ticket in this store")
                continue
            errors.append(f"{where}: depends_on {dep!r} is neither a key in this plan nor a ticket id")
    for cycle in _key_cycles(plan):
        first = plan.block(cycle[0])
        errors.append(
            f"line {first.line if first else 0}: dependency cycle between plan keys: "
            + " -> ".join(cycle + [cycle[0]])
        )
    return errors


def _key_cycles(plan: Plan) -> List[List[str]]:
    graph = {b.key: [d for d in b.list_field("depends_on") if plan.block(d)] for b in plan.blocks}
    cycles: List[List[str]] = []
    seen_keys: set = set()
    state: Dict[str, int] = {}

    def walk(node: str, chain: List[str]) -> None:
        state[node] = 1
        chain.append(node)
        for dep in graph.get(node, []):
            if state.get(dep) == 1:
                cycle = chain[chain.index(dep):]
                marker = tuple(sorted(cycle))
                if marker not in seen_keys:
                    seen_keys.add(marker)
                    cycles.append(cycle)
            elif state.get(dep, 0) == 0:
                walk(dep, chain)
        chain.pop()
        state[node] = 2

    for key in list(graph):
        if state.get(key, 0) == 0:
            walk(key, [])
    return cycles


def _ordered(blocks: Sequence[PlanBlock], plan: Plan) -> List[PlanBlock]:
    """Blocks with their in-plan dependencies first; stable otherwise."""
    by_key = {b.key: b for b in blocks}
    done: set = set()
    order: List[PlanBlock] = []

    def visit(block: PlanBlock, chain: set) -> None:
        if block.key in done or block.key in chain:
            return
        chain.add(block.key)
        for dep in block.list_field("depends_on"):
            if dep in by_key:
                visit(by_key[dep], chain)
        chain.discard(block.key)
        done.add(block.key)
        order.append(block)

    for block in blocks:
        visit(block, set())
    return order


# --------------------------------------------------------------------------
# Writing the plan file back
# --------------------------------------------------------------------------


def _render_cut_table(mapping: Dict[str, str]) -> List[str]:
    rows = [CUT_HEADING, "", "| key | id |", "|---|---|"]
    rows.extend(f"| {key} | {mapping[key]} |" for key in mapping)
    rows.append("")
    return rows


def _with_cut_table(text: str, mapping: Dict[str, str]) -> str:
    lines = text.splitlines()
    skip = _skip_map(lines)
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if skip[index] or not line.startswith("## "):
            continue
        if start is None and line[3:].strip().lower() == "cut tickets":
            start = index
        elif start is not None:
            end = index
            break
    table = _render_cut_table(mapping)
    if start is None:
        body = lines + ([""] if lines and lines[-1].strip() else []) + table
    else:
        body = lines[:start] + table + lines[end:]
    return "\n".join(body).rstrip("\n") + "\n"


def _with_status(text: str, status: str) -> str:
    lines = text.splitlines()
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        return text
    for index in range(1, closing):
        if re.match(r"^status\s*:", lines[index]):
            lines[index] = f"status: {status}"
            break
    else:
        lines.insert(closing, f"status: {status}")
    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# Locating plans
# --------------------------------------------------------------------------


def plans_dir(store: Store) -> Path:
    return store.root / "plans"


def _plan_path(store: Store, name: str) -> Path:
    candidate = Path(name)
    if candidate.suffix == ".md" or candidate.is_absolute() or "/" in name:
        if not candidate.is_absolute():
            candidate = store.project_root / candidate
        if candidate.is_file():
            return candidate
        raise RungsError(f"no plan file at {name}")
    path = plans_dir(store) / f"{name}.md"
    if path.is_file():
        return path
    raise RungsError(
        f"no plan {name!r} in {plans_dir(store)}; `rungs plan list` shows what is there"
    )


def _load(store: Store, name: str) -> Plan:
    path = _plan_path(store, name)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RungsError(f"cannot read {path}: {exc}")
    return parse_plan(text, path=path, slug=path.stem)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

SCAFFOLD = """---
slug: {slug}
title: {title}
status: draft
owner: {owner}
epic: {epic}
created: '{created}'
---

## Goal

What this plan is for, in plain words.

## Architecture

How the pieces fit together, and what already exists.

## Decisions

Choices made here that the executors must not reopen.

## Tickets

<!-- One block per ticket. Copy the example, remove this comment.

### example-key — One line title
- kind: implement
- skill: medium
- domain: python
- priority: 3
- tags: example
- scope: src/example.py, tests/test_example.py
- depends_on: none

#### Goal
What must be true when this ticket is done.

#### Decisions already made
Choices the executor must not reopen.

#### Constraints
Rules to work under: no dependency changes, no edits outside the scope.

#### Acceptance criteria
- Checkable statements the reviewer verifies.

#### Evidence required
The commands run and their output.

-->
"""


def cmd_plan_new(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    store = ctx.store
    slug = args.slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise RungsError(f"plan slug {args.slug!r} must match [a-z0-9][a-z0-9-]*")
    plans_dir(store).mkdir(parents=True, exist_ok=True)
    path = plans_dir(store) / f"{slug}.md"
    text = SCAFFOLD.format(
        slug=slug,
        title=miniyaml.quote(one_line(args.title, 200)),
        owner=agent.id,
        epic=(args.epic or "null"),
        created=now(),
    )
    try:
        create_exclusive(text, path)
    except TicketError:
        raise RungsError(f"{path} already exists")
    store.log_event("plan", None, agent.id, None, "draft", slug)
    if ctx.json_output:
        ctx.emit_json({"slug": slug, "path": str(path), "status": "draft"})
    else:
        ctx.out(str(path))
        ctx.info(f"plan {slug} scaffolded; write the tickets under '## Tickets', then `rungs cut {slug}`")
    return None


def cmd_plan_list(args, ctx: Context) -> Optional[int]:
    store = ctx.store
    folder = plans_dir(store)
    rows = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            plan = parse_plan(path.read_text(encoding="utf-8"), path=path, slug=path.stem)
            rows.append({"slug": plan.slug, "title": plan.title, "status": plan.status,
                         "epic": plan.epic, "tickets": len(plan.blocks), "cut": len(plan.cut),
                         "path": str(path), "errors": len(plan.errors)})
        except RungsError as exc:
            rows.append({"slug": path.stem, "title": f"(unreadable: {exc})", "status": None,
                         "epic": None, "tickets": 0, "cut": 0, "path": str(path), "errors": 1})
    if ctx.json_output:
        ctx.emit_json(rows)
    elif rows:
        ctx.out(views.plan_table(rows))
    if not rows:
        raise NoMatch(f"no plans in {folder}")
    return None


def cmd_plan_show(args, ctx: Context) -> Optional[int]:
    store = ctx.store
    plan = _load(store, args.slug)
    if ctx.json_output:
        data = plan.to_dict()
        data["errors"] = plan.errors + validate_plan(plan, store.existing_ids())
        ctx.emit_json(data)
        return None
    ctx.out(plan.text.rstrip("\n"))
    errors = plan.errors + validate_plan(plan, store.existing_ids())
    if errors:
        ctx.warn(f"{len(errors)} problem(s) would stop `rungs cut`:")
        for line in errors:
            ctx.warn(f"  {line}")
    return None


def cmd_plan(args, ctx: Context) -> Optional[int]:
    # Reached only when no subcommand was given; argparse handles the rest.
    ctx.subparsers["plan"].print_help(ctx.stdout)
    return None


def cmd_cut(args, ctx: Context) -> Optional[int]:
    agent = ctx.acting_agent(args)
    store = ctx.store
    plan = _load(store, args.plan)
    known_ids = store.existing_ids()
    errors = plan.errors + validate_plan(plan, known_ids)
    if errors:
        raise RungsError(
            f"{plan.path}: {len(errors)} problem(s); nothing was created:\n  " + "\n  ".join(errors)
        )
    if not plan.blocks:
        raise RungsError(f"{plan.path} lists no tickets under '## Tickets'")

    pending = [b for b in plan.blocks if b.key not in plan.cut]
    if not pending:
        message = f"every ticket in {plan.slug} is already cut ({len(plan.cut)})"
        if ctx.json_output:
            ctx.emit_json({"plan": plan.slug, "created": [], "skipped": list(plan.cut), "dry_run": args.dry_run})
            return None
        ctx.out(message)
        return None

    ordered = _ordered(pending, plan)
    resolved: Dict[str, str] = dict(plan.cut)
    created: List[Tuple[str, Ticket]] = []

    if args.dry_run:
        rows = []
        for block in ordered:
            deps = [resolved.get(d, f"(new: {d})") for d in block.list_field("depends_on")]
            rows.append([block.key, block.fields.get("kind", ""), block.fields.get("skill", ""),
                         block.fields.get("priority", "") or "-", ", ".join(deps) or "-",
                         views.truncate(block.title, 50)])
        if ctx.json_output:
            ctx.emit_json({"plan": plan.slug, "would_create": [b.to_dict() for b in ordered],
                           "skipped": sorted(plan.cut), "dry_run": True})
        else:
            ctx.out(views.table(["key", "kind", "skill", "pri", "depends on", "title"], rows))
            ctx.info(f"{len(ordered)} ticket(s) would be created; {len(plan.cut)} already cut")
        return None

    for block in ordered:
        depends_on = []
        for dep in block.list_field("depends_on"):
            depends_on.append(resolved.get(dep, dep))
        priority = (block.fields.get("priority") or "").strip()
        ticket = Ticket(
            id=store.new_id(),
            title=block.title,
            status="open",
            kind=block.fields["kind"].strip(),
            skill=normalise_skill(block.fields["skill"]),
            domain=one_line(block.fields["domain"]) if block.fields.get("domain") else None,
            epic=one_line(block.fields.get("epic") or plan.epic or "") or None,
            plan=plan.slug,
            priority=None if not priority or priority.lower() == "none" else int(priority),
            tags=[one_line(t) for t in block.list_field("tags")],
            scope=[scope.normalise(s) for s in block.list_field("scope")],
            depends_on=depends_on,
            created=now(),
        )
        ticket.updated = ticket.created
        for key in ("goal", "decisions", "constraints", "acceptance", "evidence_required"):
            ticket.set_section(key, block.sections.get(key, ""))
        problems = validate(ticket)
        if problems:
            raise RungsError(f"{block.key}: the ticket would not be valid: {'; '.join(problems)}")
        store.create(ticket)
        store.log_event("cut", ticket.id, agent.id, None, "open", f"{plan.slug}:{block.key}")
        resolved[block.key] = ticket.id
        created.append((block.key, ticket))

    new_text = _with_status(_with_cut_table(plan.text, resolved), "cut")
    write_atomic(new_text, plan.path)

    if ctx.json_output:
        ctx.emit_json({
            "plan": plan.slug,
            "created": [{"key": key, **ticket.to_dict()} for key, ticket in created],
            "skipped": sorted(plan.cut),
            "dry_run": False,
        })
        return None
    ctx.out(views.table(["key", "id", "skill", "title"],
                        [[key, t.id, t.skill, views.truncate(t.title, 60)] for key, t in created]))
    ctx.info(f"created {len(created)} ticket(s) from {plan.slug}"
             + (f"; {len(plan.cut)} were already cut" if plan.cut else ""))
    return None


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def register(subparsers, ctx: Context) -> None:
    plan = ctx.command(
        subparsers, "plan", cmd_plan, "plan files: new, list, show",
        description="A plan holds the design and a list of ticket blocks under '## Tickets'. "
                    "`rungs cut` turns the blocks into tickets. See docs/PLANS.md for the grammar.",
    )
    sub = plan.add_subparsers(dest="plan_command", metavar="new | list | show")

    p = sub.add_parser("new", help="scaffold a new plan file",
                       description="Write .rungs/plans/<slug>.md with the headings and one "
                                   "commented-out example ticket block.")
    p.set_defaults(handler=cmd_plan_new, command_name="plan new")
    ctx.add_agent_option(p)
    ctx.add_json_option(p)
    p.add_argument("slug", help="file name and plan id, [a-z0-9][a-z0-9-]*")
    p.add_argument("--title", required=True, help="one line: what the plan is for")
    p.add_argument("--epic", help="epic every ticket in the plan inherits unless it names its own")

    p = sub.add_parser("list", help="every plan and how much of it is cut")
    p.set_defaults(handler=cmd_plan_list, command_name="plan list")
    ctx.add_json_option(p)

    p = sub.add_parser("show", help="print a plan file and any grammar problems")
    p.set_defaults(handler=cmd_plan_show, command_name="plan show")
    ctx.add_json_option(p)
    p.add_argument("slug", help="plan slug or a path to a plan file")

    p = ctx.command(
        subparsers, "cut", cmd_cut, "create the tickets a plan lists",
        description="Validates the whole plan first and creates nothing when anything is wrong. "
                    "Keys already in the plan's '## Cut tickets' table are skipped, so a plan can "
                    "grow and be cut again; depends_on keys are resolved to the new ticket ids.",
    )
    ctx.add_agent_option(p)
    ctx.add_json_option(p)
    p.add_argument("plan", help="plan slug or a path to a plan file")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="print what would be created and change nothing")
