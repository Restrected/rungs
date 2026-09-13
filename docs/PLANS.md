# Plan files

A plan is one markdown file in `.rungs/plans/<slug>.md`. The director writes
the design and the list of tickets once; `rungs cut <slug>` creates every
ticket from it, resolves the dependencies between them, and writes the key →
id table back into the plan so the same plan can grow and be cut again.

```
rungs plan new rungs-v1 --title "Build rungs" --epic rungs-core --agent claude.fable.001
rungs plan list
rungs plan show rungs-v1
rungs cut rungs-v1 --agent claude.fable.001 --dry-run
rungs cut rungs-v1 --agent claude.fable.001
```

The whole plan is validated before anything is created. Every error is
reported with its line number and **no ticket is created while any error
stands** — a half-cut plan is never left behind.

## Grammar

This is the normative grammar (DESIGN.md section 8). Anything outside it is an
error, not a guess.

### Front matter

The file starts with a front matter between two lines that are **exactly**
`---`, in the same strict YAML subset ticket files use:

| Key | Meaning |
|---|---|
| `slug` | The plan id. Defaults to the file name when absent. |
| `title` | One line: what the plan is for. |
| `status` | `draft`, `approved`, `cut` or `done`. `cut` sets it. |
| `owner` | Agent id of whoever designed it. |
| `epic` | Every ticket inherits this epic unless its block names its own. |
| `created` | `YYYY-MM-DDTHH:MM:SS`. |

### The ticket list

The list starts at the first `## Tickets` heading and ends at the next `## `
heading or the end of the file. Prose between `## Tickets` and the first
ticket block is ignored, so the heading can carry a sentence of explanation.

Each ticket block starts with a heading:

```
### <key> — <title>
```

- `<key>` matches `[a-z0-9][a-z0-9-]{0,39}` and is unique in the plan. It is
  how other blocks depend on this one; it is not the ticket id.
- The separator is an em dash (` — `), a double hyphen (` -- `) or a colon
  (`: `).
- Duplicate keys are an error.

### Fields

Immediately after the heading comes a bullet list of `- field: value` lines.
Only these fields are recognised; anything else is an error:

| Field | Required | Value |
|---|---|---|
| `kind` | yes | `design` `implement` `fix` `review` `check` `research` `doc` `chore` |
| `skill` | yes | `very-low` `low` `medium` `high` `xhigh` (aliases such as `3` or `very-high` are accepted) |
| `domain` | no | routing hint, e.g. `ui`, `python`, `security` |
| `priority` | no | integer, lower is more urgent, or `none` |
| `tags` | no | comma separated |
| `scope` | no | comma separated paths the ticket may change |
| `depends_on` | no | comma separated plan keys or existing ticket ids |
| `epic` | no | overrides the plan's epic for this ticket |

List values are comma separated, whitespace around each item is trimmed, and
`none` or an empty value means an empty list. A path containing a comma is not
supported. A field given twice in one block is an error.

`depends_on` items are plan keys or ticket ids that already exist in the
store. Anything else is an error, as is a cycle between plan keys.

Scope entries follow the matching contract of DESIGN.md section 6: a trailing
`/` is a directory prefix, `*` and `?` do not cross a `/`, and `**` matches any
number of directories. An empty scope means the ticket changes **no files**
(a design or research ticket), not "any file".

### Sections

After the fields come the ticket's sections, in any order:

```
#### Goal
#### Decisions already made
#### Constraints
#### Acceptance criteria
#### Evidence required
```

`Goal` and `Acceptance criteria` are required and must not be empty; the rest
are optional. (`Decisions`, `Acceptance` and `Evidence` are accepted as short
spellings.) Any other `#### ` heading is an error.

Text inside a fenced code block (``` or `~~~`) is copied into the ticket
verbatim, and headings and bullets inside a fence are not read as grammar.
Text inside an HTML comment (`<!-- ... -->`) is ignored entirely, which is how
the scaffold carries its commented-out example.

## What `cut` does

1. Parses and validates the whole plan; **on any error it prints every problem
   with its line number and creates nothing**.
2. Skips every key already listed in the plan's `## Cut tickets` table.
3. Creates the remaining tickets in dependency order, in `open/`, with
   `plan: <slug>` and the plan's `epic` where the block has none, and resolves
   each `depends_on` key to the id of the ticket it created.
4. Appends or updates the `## Cut tickets` table (`| key | id |`) in the plan
   file and sets the front matter `status: cut`.
5. Logs one `cut` event per ticket.

`--dry-run` prints what would be created and changes nothing.

## A complete plan

```markdown
---
slug: rungs-intake
title: 'Intake: work from outside systems'
status: draft
owner: claude.fable.001
epic: rungs-core
created: '2026-09-13T02:30:00'
---

## Goal

Outside systems drop one JSON file per request into `.rungs/inbox/` and a
scheduler turns them into tickets. They never write ticket files themselves.

## Architecture

`intake.py` validates and sanitises each file, deduplicates by `origin`,
creates the ticket in `triage/` (or `open/` when the source is trusted and the
request carries acceptance criteria) and writes a result file beside it.

## Decisions

- Ticket text is data. Quoted user text is labelled as the requester's words.
- A file that fails validation is moved aside with the error; nothing is
  created.

## Tickets

The two blocks below are cut into one ticket each.

### intake — Turn inbox JSON into triage tickets
- kind: implement
- skill: medium
- domain: python
- priority: 3
- tags: intake, inbox
- scope: src/rungs/intake.py, tests/test_intake.py

#### Goal
`rungs intake` reads every `*.json` in `.rungs/inbox/`, validates it against
the contract in DESIGN.md section 7, and creates one ticket per valid request.

#### Decisions already made
The inbox is the only folder an outside system writes to. Sanitising runs on
every string: control characters removed, one-line fields flattened, any run of
three or more dashes broken, lengths capped.

#### Constraints
Standard library only. No network. Never overwrite an existing ticket file.

#### Acceptance criteria
- a valid request creates a ticket in `triage/` and a `.result.json` beside the
  processed file
- a second file with the same `source` and `key` gives outcome `duplicate` and
  creates nothing
- an invalid file gives outcome `invalid` with the reason and creates nothing

#### Evidence required
The unittest summary line and the result JSON of each of the three cases.

### intake-docs — Document the intake contract
- kind: doc
- skill: low
- depends_on: intake
- scope: docs/INTAKE.md

#### Goal
A page an application developer can follow to drop a request into the inbox,
with the field table and one worked example.

#### Acceptance criteria
- every field of the contract table appears with its type and whether it is
  required
- the example JSON is one a reader can copy and run through `rungs intake`
```

Cutting that plan creates two tickets and writes back:

```markdown
## Cut tickets

| key | id |
|---|---|
| intake | rg-3f9a1 |
| intake-docs | rg-7c204 |
```

`rg-7c204` carries `depends_on: [rg-3f9a1]`, so `rungs next` does not offer the
documentation ticket until the implementation is closed.
