# rungs — design

`rungs` is a file-based ticket system for delegating software work to a mixed
crew of AI agents (and people) of different ability. Tickets are rungs on a
ladder: every ticket names the lowest skill level that can work it, every agent
is registered with a skill level, and the tool only offers an agent the rungs
it can reach. An orchestrator (the "director") designs the work in one plan
file, cuts it into tickets, and watches the results come back through a review
gate. Outside systems (an application's "request a change" button, a support
queue, a cron job) drop work into an inbox and a scheduler turns it into
tickets.

It descends from `arbite` (https://github.com/mattaaron79/arbite), keeps the
parts of it that proved right, and replaces the parts that did not. Version 1
of this design was critiqued by Astra (Codex gpt-6-astra) on 2026-09-13; the
critique's ten findings were all adopted and are marked "(critique N)" below.

## 1. What is kept from arbite and what changes

Kept, because it worked:

- Tickets are markdown files with a front matter, stored in a git repository,
  no database and no server. The folder a ticket sits in is its status, and
  every command keeps the front matter in step with the folder.
- Writes are atomic (temp file plus rename); a claim is an exclusive create so
  two agents cannot both believe they own a ticket.
- `--json` on every reading command, stable exit codes, `doctor --fix`.
- `depends_on` ordering, `epic` grouping, numeric `priority` (lower is more
  urgent), attributed timestamped notes.
- An `AGENTS.md` generated from the tool itself so it never drifts.
- Liveness is not the tool's job: a claim never expires by itself. The tool
  reports stale claims; a director decides.

Changed, because it failed or was missing:

| Problem in arbite | rungs |
|---|---|
| The file is split on the substring `---` anywhere, so any value containing three dashes corrupts the store and every listing fails until repaired by hand. | Front matter ends only at a line that is exactly `---`. Values are quoted on write. The parser is the tool's own strict subset, no PyYAML dependency. |
| Agent ids with `/` break the config. | Ids are validated (`company.model.instance`, `[a-z0-9.-]`) when the roster is read and when an id is passed on the command line. |
| Tier is self-assessed by the agent from its model name. | A roster (`rungs.yaml`) assigns each agent a skill level. `rungs next --agent X` reads it; a claim above the agent's level is refused. |
| Four tiers named low/medium/high/frontier. | Five named levels, very-low to xhigh, with a numeric rank so "at or below" is well defined. |
| A ticket goes from in_progress straight to closed; nobody else has to look. | A `review` status between `in_progress` and `closed`. The executor submits with evidence; a different agent reviews and either accepts or returns it. |
| No notion of which files a ticket may touch. Two executors edit the same file. | `scope` (path globs) on every ticket; claiming a ticket whose scope overlaps another live ticket is refused or warned per policy; `rungs verify` compares the declared or actual change set with the scope. |
| A ticket body is free text. Delegations forget the goal, constraints or acceptance. | A fixed section template: Goal, Decisions already made, Constraints, Acceptance criteria, Evidence required, Evidence, Review, Notes. |
| Outside systems must write the ticket file format themselves. | `inbox/` plus `rungs intake`: outside systems drop a small JSON file; the tool validates, sanitises, deduplicates by origin, creates the ticket in `triage/` and writes a result file the producer can read. |
| The director creates tickets one command at a time. | A plan file holds the design and a ticket table; `rungs cut <plan>` creates every ticket with dependencies resolved, idempotently, after validating the whole plan. |
| `pip install` of a checkout. | `deploy.sh` (or `rungs deploy`) links the CLI onto the PATH or vendors it into the project, initialises the store, registers the agents given on the command line, writes the docs and runs `doctor`. |
| Migration is manual. | `rungs import-arbite` converts an existing `.arbite/` store. |

## 2. Store layout

```
<project>/
  rungs.yaml              roster and policy (hand edited, committed)
  .rungs/
    AGENTS.md             generated workflow guide: quick start, roster, rules, command reference
    inbox/                intake files from outside systems (JSON), not tickets yet
    inbox/done/           processed intake files, each with a <name>.result.json beside it
    triage/               tickets that exist but are not ready to be worked (intake lands here)
    open/                 ready and unclaimed
    in_progress/          claimed, being worked
    review/               submitted with evidence, waiting for a reviewer
    blocked/              stalled; blocked_by says why
    shelved/              parked
    closed/YYYY-MM/       accepted, archived by close month
    plans/                plan files (design plus ticket table); rungs cut reads them
    agents/               one file per agent: skill, role, what it is doing
    events.jsonl          append-only log of every state change
```

Status is the folder. `rungs doctor` finds and repairs drift.

## 3. Ticket file

Filename `<id>.md`. New ids are `rg-` plus five hex characters. Imported ids
(`tic-xxxx`) are kept, so the id pattern is `^[a-z]{1,8}-[0-9a-f]{4,8}$`.

```
---
id: rg-3f9a1
title: 'Intake: turn inbox JSON into open tickets'
status: open
kind: implement
skill: medium
domain: python
epic: rungs-core
plan: rungs-v1
priority: 3
tags: [intake, inbox]
scope: ['src/rungs/intake.py', 'tests/test_intake.py']
assignee: null
reviewer: null
depends_on: [rg-1c0de]
blocked_by: null
origin: null
verdict: null
review_rounds: 0
files: []
created: '2026-09-13T02:30:00'
updated: '2026-09-13T02:30:00'
submitted: null
closed: null
---

## Goal
What must be true when this ticket is done, in plain words.

## Decisions already made
Choices the executor must not reopen.

## Constraints
Rules to work under (no dependency changes, no edits outside scope, ...).

## Acceptance criteria
- Checkable statements the reviewer verifies.

## Evidence required
What the executor must attach at submit time (files changed, commands run, output).

## Evidence
(filled by `rungs submit`)

## Review
(filled by `rungs review`)

## Notes
- 2026-09-13T02:31:00 claude.opus.001: claimed
```

Field vocabulary:

| Field | Values | Meaning | Set by |
|---|---|---|---|
| `id` | `rg-xxxxx` | Never changes. | create, cut, intake, import |
| `title` | one line, 200 chars | | create, set |
| `status` | `triage` `open` `in_progress` `review` `blocked` `shelved` `closed` | Mirrors the folder. | lifecycle commands only |
| `kind` | `design` `implement` `fix` `review` `check` `research` `doc` `chore` | What sort of work. | create, set |
| `skill` | `very-low` `low` `medium` `high` `xhigh` (ranks 1–5) | Lowest agent level that can work it. Not urgency, not specialisation. | create, set |
| `domain` | free text | Routing hint: `ui`, `php`, `python`, `security`, `architecture`. | create, set |
| `epic` | free text | Grouping label. | create, set |
| `plan` | plan slug | The plan file this ticket was cut from. | cut |
| `priority` | integer, lower first | Urgency. Unset sorts last. | create, set |
| `tags` | list | Search labels. | create, set |
| `scope` | list of path patterns | Files the ticket may change. Empty means "no files" (design, research), not "any file". | create, set |
| `assignee` | agent id | Who holds it. | claim, release, reassign |
| `reviewer` | agent id | Who reviewed it last. | review |
| `depends_on` | ticket ids | Must be closed before this is workable. | create, depend |
| `blocked_by` | text or id | Why it is stalled. | block, unblock |
| `origin` | `source:key` | Where an intake ticket came from; unique across the store. | intake, import |
| `verdict` | `accepted` `returned` `null` | Outcome of the last review; cleared by reopen. | review, reopen |
| `review_rounds` | integer | How many times it went through review. | review |
| `files` | list of paths | The change set the executor declared at submit. | submit |
| `created` `updated` `submitted` `closed` | `YYYY-MM-DDTHH:MM:SS` local | | lifecycle commands |

Input aliases accepted for `skill`: `vlow`, `xlow`, `1` … `xhigh`, `very-high`,
`5`. Output always uses the canonical name.

`rungs set` may change only `title`, `kind`, `skill`, `domain`, `epic`,
`priority`, `tags`, `scope` and the body sections (`goal`, `decisions`,
`constraints`, `acceptance`, `evidence_required`). Every other field belongs
to a lifecycle command and `set` refuses it (critique 1).

## 4. Roster and policy: `rungs.yaml`

The roster is the document that tells every agent its own level. It is
committed with the project, rendered into `.rungs/AGENTS.md`, and copied into
the header of each agent's file under `.rungs/agents/`.

```yaml
project: example-app
agents:
  claude.fable.001:
    skill: xhigh
    role: director          # director | executor | reviewer | critic
    floor: high             # lowest ticket level `next` offers it by default (optional)
  openai.astra.001:
    skill: high
    role: executor
  claude.opus.001:
    skill: medium
    role: executor
  claude.opus.reviewer:
    skill: high
    role: reviewer
  claude.sonnet.001:
    skill: low
    role: executor
  claude.haiku.001:
    skill: very-low
    role: executor
policy:
  review_required: true         # close needs an accepted review unless a director passes --no-review
  scope_overlap: refuse         # refuse | warn | allow
  unregistered_agents: refuse   # refuse | warn
  stale_after_hours: 24         # `rungs stale` threshold
intake:
  default_skill: medium
  default_kind: fix
  default_priority: 15
  sources:
    helpdesk:
      epic: user-requests
      domain: ui
      tags: [user-request]
      ready: false              # true: complete requests go straight to open/
```

Rules derived from the roster:

- Every state-changing command takes `--agent` (default: `RUNGS_AGENT` in the
  environment). An id that is not in the roster is refused, or warned about,
  per `policy.unregistered_agents`, on every such command (critique 1).
- An agent may claim a ticket whose skill rank is at or below its own.
  `--force` overrides and writes a note saying so.
- `rungs next --agent X` returns workable tickets with rank ≤ X's skill,
  ordered: tickets at X's own level first, then descending level, and within a
  level by priority then age. `floor` is a preference, not a rule (critique 7):
  tickets below the floor are left out of `next` by default, and when nothing
  is left above the floor the command says on stderr how many workable tickets
  sit below it; `--ignore-floor` shows them, and a direct `claim` never looks
  at the floor.
- Roles: `director` and `reviewer` may review; the reviewer must not be the
  assignee. Only `director` may `close --no-review`, `reassign`, or
  `release --force` another agent's ticket (critique 1, 4).

## 5. Lifecycle

```
inbox ──intake──▶ triage ──ready──▶ open ──claim──▶ in_progress ──submit──▶ review ──accept──▶ closed
                                     ▲               │    ▲                   │
                                     │ release       │    │ return (findings) │
                                     └───────────────┘    └───────────────────┘
open/in_progress/review ──block──▶ blocked ──unblock──▶ see the recovery table
open ──shelve──▶ shelved ──unshelve──▶ open
closed ──reopen──▶ open (assignee, verdict, reviewer, submitted, files cleared)
```

- `ready` (any registered agent) moves a `triage` ticket to `open` after the
  triager has filled in what was missing; it refuses when title, goal, kind,
  skill or acceptance criteria are still empty (critique 5).
- `claim` checks in this order: registered agent, ticket is `open`, skill,
  dependencies closed, scope overlap against every `in_progress` and `review`
  ticket, then the exclusive create.
- `submit` (assignee only) needs at least one of `--summary`, `--files`,
  `--checks`, `--evidence-file` and writes the Evidence section from them
  (check output is fenced so pasted terminal text cannot end the section), records `submitted` and `files`,
  moves to `review/`. The assignee stays so a return goes back to them.
- `review --verdict accept` sets `verdict: accepted`, `reviewer`, and closes
  the ticket into `closed/YYYY-MM/`. `--verdict return` requires `--findings`
  or `--findings-file`, appends them to the Review section, increments `review_rounds`, sets `verdict: returned`
  and moves the ticket back to `in_progress/` for the same assignee (or to
  `open/` with `--release` when the assignee is gone).
- `close` on a ticket without `verdict: accepted` is refused unless a
  `director` passes `--no-review`; the bypass is written to the Notes and to
  the event log. `review --force` (a director reviewing a ticket it holds, or
  reviewing without the reviewer role) is likewise director-only and logged.
  These are the only two bypasses; an executor can never accept its own work
  (critique 1, review finding).
- `reopen` moves a closed ticket to `open/` and clears `assignee`, `verdict`,
  `reviewer`, `submitted`, `files` and `closed`, so an old acceptance can never
  cover new work (critique 1).
- Every transition appends a line to `events.jsonl`:
  `{"at": ..., "agent": ..., "action": "claim", "ticket": "rg-…", "from": "open", "to": "in_progress", "detail": ...}`.

Recovery table (critique 4). "Overlap recheck" means the same check `claim`
runs; when it fails the ticket goes to `open/` with the assignee cleared and a
note saying why.

| Situation | Command | Who | Result |
|---|---|---|---|
| Executor stops part-way | `release ID` | the assignee | `open`, assignee cleared, note with reason |
| Executor gone, ticket held | `release ID --force` | director | same, note records the takeover |
| Ticket should go to someone else | `reassign ID --to B` | director | stays in its status; B must pass the skill check; overlap recheck; note |
| Stalled on something outside | `block ID --reason` | assignee or director | `blocked`; assignee kept |
| Blocker cleared | `unblock ID` | any registered agent | back to `in_progress` if it has an assignee and the overlap recheck passes, otherwise `open` |
| Claim looks abandoned | `stale [--hours N]` | anyone | lists `in_progress`/`review` tickets with no event for N hours (default from policy); no automatic change |
| Reviewer disagrees but the assignee is gone | `review --verdict return --release` | reviewer | `open`, findings kept, assignee cleared |

## 6. File scope and "watching their changes"

Scope entries are paths relative to the project root, with an explicit
matching contract (critique 3):

- An entry ending in `/` is a **directory prefix**: it covers every file
  beneath it at any depth.
- An entry without wildcards is a **literal file**.
- An entry with `*`, `?` or `[...]` is a **glob**. `*` and `?` never cross a
  `/`; `**` matches any number of directories. So `src/*.py` covers one level
  and `src/**/*.py` covers every level.

"Covers" (used by `verify`): a path is covered when some entry is a directory
prefix of it, equals it, or glob-matches it under the rules above.

"Overlaps" (used by `claim`, `reassign`, `unblock`, `doctor`): two entries
overlap when they could cover a common path. The implementation is exact for
literal/literal and literal/glob pairs and conservative for glob/glob pairs:

| A | B | Overlap | Why |
|---|---|---|---|
| `src/a.py` | `src/b.py` | no | different literals |
| `src/a.py` | `src/` | yes | prefix |
| `src/` | `src/sub/` | yes | prefix |
| `src/*.py` | `src/a.py` | yes | glob covers the literal |
| `src/*.py` | `src/sub/a.py` | no | `*` does not cross `/` |
| `src/*.py` | `src/test_*` | yes | globs: literal prefixes `src/` and `src/test_` are prefix-comparable |
| `src/*.py` | `docs/*.md` | no | literal prefixes differ |
| `src/**` | `src/sub/x.py` | yes | `**` crosses directories |
| `**/*.md` | `docs/` | yes | literal prefix of the glob is empty, which is comparable with everything |

A wide entry (`src/`, `**/*.md`) therefore locks out everything beneath it
while the ticket is `in_progress` or `review`. That is intended; the policy
`scope_overlap: warn` exists for teams that prefer a warning.

`rungs verify ID` judges a ticket-specific change set (critique 2), never the
whole working tree by default:

1. the `files` the executor declared at submit (default);
2. `--tree` adds the working tree's changed and untracked files, `--staged`
   the index, `--since REF` the commits after REF; the output labels these
   "tree" paths and says they may include another agent's concurrent edits;
3. `.rungs/` and `rungs.yaml` are always ignored;
4. `--files a,b` checks an explicit list.

Every path outside the scope is listed; exit 3 when there are any. Declared
files that the tree does not show as changed are listed too, as a warning.

## 7. Intake: work from outside systems

Outside systems never write ticket files. They drop one JSON file per request
into `.rungs/inbox/` (any filename ending in `.json`), or call
`rungs intake --file request.json`, and a scheduler runs `rungs intake`.

Contract (critique 6):

| Field | Type | Required | Rule |
|---|---|---|---|
| `source` | string `[a-z0-9-]{1,40}` | yes | Must exist under `intake.sources` in `rungs.yaml` unless `intake.allow_unknown_sources: true`. |
| `key` | string ≤ 120 | yes | `origin` is `source:key`; unique across the whole store. |
| `title` | string ≤ 200 | yes | One line. |
| `goal` | string ≤ 20 000 | yes | Becomes the Goal section. |
| `kind` | vocabulary | no | Falls back to source block, then `intake.default_kind`. |
| `skill` | vocabulary or alias | no | Falls back likewise. |
| `priority` | integer | no | Falls back likewise. |
| `domain`, `epic` | string ≤ 80 | no | Falls back likewise. |
| `tags` | list of strings ≤ 40 | no | Merged with the source block's tags. |
| `scope` | list of strings ≤ 300 | no | Kept as given. |
| `acceptance` | string ≤ 20 000 | no | Acceptance criteria section. |
| `context` | object of string → string | no | Rendered as a bullet list under Goal. |
| `quoted` | string ≤ 20 000 | no | Rendered under a heading saying it is the requester's own words, as data. |
| `requested_by`, `approved_by` | string ≤ 200 | no | Rendered under Goal. |
| anything else | | | Ignored, listed in the result as `ignored_fields`. |

Processing rules:

- Every string is sanitised: control characters removed, line breaks in
  one-line fields flattened, any run of three or more dashes broken so the
  quoted text can never look like a front matter boundary even to another
  tool, lengths capped.
- A file that fails validation is moved to `inbox/done/` with outcome
  `invalid` and the error; nothing is created. A duplicate origin gives
  outcome `duplicate` with the existing ticket id. Otherwise `created`.
- Each processed file gets `inbox/done/<name>.result.json`:
  `{"source", "key", "outcome", "ticket", "error", "ignored_fields", "at"}`.
  `--json` prints the same records. `--delete` removes the files instead of
  keeping them.
- The new ticket lands in `triage/` unless the source block says `ready: true`
  **and** the request carries `acceptance`; then it lands in `open/`
  (critique 5). `rungs triage` lists what waits for a triager.
- The tool also accepts arbite-format `.md` files in the inbox (front matter
  parsed leniently), so an existing exporter can be pointed at `.rungs/inbox`
  without a change; `origin` is then `arbite:<id>`.
- `rungs request --source S --key K --title T --goal G ...` builds the same
  request from command-line options and runs it through the same validation
  and creation path immediately (exit 0 created, 2 duplicate, 1 invalid);
  `--defer` writes it into the inbox for the scheduled intake instead. A
  producer that can run a command therefore never writes JSON either.
- Scheduler line: `*/5 * * * * cd /path/to/project && rungs intake --quiet`.
- What happens before the inbox (how an application collects a request and
  who approves it) is the application's business. rungs takes over at the
  inbox and knows nothing about any host project.

## 8. Plans: the orchestrator designs once, cuts many tickets

A plan is a markdown file in `.rungs/plans/<slug>.md` with a front matter and a
ticket list the director writes while designing. Grammar (critique 8):

- Front matter: `slug`, `title`, `status` (`draft` `approved` `cut` `done`),
  `owner`, `epic`, `created`.
- The ticket list starts at the first `## Tickets` heading and ends at the
  next `## ` heading or the end of the file.
- Each ticket starts with `### <key> — <title>`; the separator is an em dash,
  a double hyphen (` -- `) or a colon (`: `), surrounded by spaces. `<key>` is
  `[a-z0-9][a-z0-9-]{0,39}` and unique in the plan. Duplicate keys are an
  error.
- Immediately after the heading, a bullet list of `- field: value` lines.
  Recognised fields: `kind`, `skill`, `domain`, `priority`, `tags`, `scope`,
  `depends_on`, `epic`. List values are comma-separated; whitespace around
  items is trimmed; a path containing a comma is not supported; `none` or an
  empty value means an empty list. Unknown fields are an error.
- Then `#### Goal`, `#### Decisions already made`, `#### Constraints`,
  `#### Acceptance criteria`, `#### Evidence required` in any order, each
  optional except Goal and Acceptance criteria. Text inside fenced code blocks
  is copied verbatim and headings inside fences are ignored.
- `depends_on` items are plan keys or existing ticket ids. An unknown key or
  id is an error, and so is a cycle.
- `kind` and `skill` are required in every block: a ticket cannot exist
  without them and the plan defines no default.
- Text inside an HTML comment (`<!-- ... -->`) is ignored, which is how the
  scaffold from `rungs plan new` carries a commented example block.
- The three long section headings may be shortened to `#### Decisions`,
  `#### Acceptance` and `#### Evidence`.
- The whole plan is validated first; errors are reported with line numbers and
  no ticket is created when any exists.

`rungs cut <slug>` (or a path) creates one ticket per block, resolves
`depends_on`, sets `plan: <slug>` and the plan's `epic` where the block has
none, writes a `## Cut tickets` table (key → id) back into the plan file and
marks the plan `cut`. Keys already listed in that table are skipped, so a plan
can grow and be cut again. `--dry-run` prints what would be created. `rungs
plan new <slug>` scaffolds the file; `rungs plan list` and `rungs plan show
<slug>` read them.

The markdown ticket format is deliberately not YAML: nested YAML needs a full
parser, and headings with a bullet list are what a person or a model writes
naturally.

## 9. Command line

Exit codes: `0` success, `1` error, `2` query ran but matched nothing,
`3` integrity or scope violations found, `4` refused by policy (skill, overlap,
reviewer is the author, unregistered agent, review bypass by a non-director,
an agent acting on a ticket it does not hold, a claim on a ticket that is not
`open`, since every claim check is a policy check). A command run on a ticket
in the wrong status for it (`submit` on an open ticket, `close` on a closed
one, `ready` on a non-triage ticket) is an ordinary error, exit 1: nothing
about policy stopped it, the request itself made no sense.

`--agent ID` is required on every command marked (A); `RUNGS_AGENT` in the
environment supplies the default. `--json` is accepted on every reading
command.

```
rungs init [--project NAME] [--agent ID:SKILL[:ROLE]]...   create .rungs/, rungs.yaml, AGENTS.md
rungs deploy [--into DIR] [--bin DIR | --vendor] [--agent ID:SKILL[:ROLE]]... [--import-arbite [PATH]]
rungs docs                                       regenerate .rungs/AGENTS.md and agent file headers
rungs agents                                     roster with skill, role and current ticket
rungs whoami (A)                                 the agent's level, role, and what it may claim

rungs create (A) --title T --kind K --skill S [--domain --epic --priority --tags --scope
             --depends-on --goal --decisions --constraints --acceptance --evidence-required
             --body-file F | --from-json F] [--triage]
rungs plan new SLUG --title T [--epic E] (A) | list | show SLUG
rungs cut SLUG|PATH (A) [--dry-run]
rungs request --source S --key K --title T --goal G [the JSON fields as options] [--defer]
rungs intake [--file F] [--dry-run] [--delete] [--quiet]
rungs triage                                     tickets waiting to be made ready
rungs ready ID (A)                               triage → open, after the checks
rungs import-arbite [PATH] [--dry-run]

rungs list [--status --skill --kind --domain --epic --plan --assignee --tag --count]
rungs next (A) [--count N] [--claim] [--epic E] [--domain D] [--ignore-floor]
rungs board [--epic E]
rungs show ID
rungs search TEXT
rungs deps ID
rungs log [ID] [--since TIMESTAMP] [--count N]
rungs stale [--hours N]

rungs claim ID (A) [--force] [--allow-overlap]
rungs release ID (A) [--reason R] [--force]
rungs reassign ID (A) --to AGENT [--reason R]
rungs note ID (A) MESSAGE
rungs block ID (A) --reason R
rungs unblock ID (A) [--reason R]
rungs shelve ID (A) [--reason R]
rungs unshelve ID (A) [--reason R]
rungs submit ID (A) [--summary S] [--files a,b] [--checks C] [--evidence-file F]
rungs review ID (A) --verdict accept|return [--findings TEXT | --findings-file F] [--release] [--force]
rungs close ID (A) [--no-review] [--reason R]
rungs reopen ID (A) [--reason R]
rungs set ID (A) FIELD VALUE [FIELD VALUE ...]
rungs depend ID (A) --on ID2 | --clear
rungs verify ID [--tree] [--staged] [--since REF] [--files a,b]
rungs doctor [--fix]
rungs --version
```

`next --count N` (default 1) returns up to N tickets; with `--claim` each is
claimed one by one, a race lost on one ticket does not stop the others, and
the count actually claimed is reported on stderr and in the JSON (critique 9).

## 10. Deploy

The repository is cloned once anywhere (`~/rungs`). Then, for each project:

```
~/rungs/deploy.sh --into /path/to/project \
    --agent claude.fable.001:xhigh:director \
    --agent claude.opus.001:medium \
    --agent claude.opus.reviewer:high:reviewer
~/rungs/deploy.sh --into /path/to/project --vendor            # copy the tool into <project>/tools/rungs
~/rungs/deploy.sh --into /path/to/project --import-arbite     # convert an .arbite/ store as well
```

`deploy.sh` is a thin wrapper over `bin/rungs deploy`, so the logic lives in
one place. "Deployed" means all of the following are true when the command
exits 0 (critique 10):

1. `rungs` resolves on the PATH: a symlink in `--bin` (default `~/.local/bin`)
   to the clone's `bin/rungs`, or, with `--vendor`, a copy of `bin/` and
   `src/rungs/` under `<project>/tools/rungs/` plus a wrapper at
   `<project>/tools/rungs/rungs`; the command warns when the bin directory is
   not on the PATH;
2. `<project>/.rungs/` exists with every folder of section 2;
3. `<project>/rungs.yaml` exists; the `--agent` entries are in it (added to an
   existing file, never duplicated), and without any `--agent` a template with
   commented examples is written and the command says agents must be added
   before work can be claimed;
4. `.rungs/AGENTS.md` and the agent files are written;
5. an `.arbite/` store was imported when asked;
6. `rungs doctor` passes;
7. the two lines to add to the project's `CLAUDE.md` or `AGENTS.md` are printed.

Prerequisites: Python 3.9 or newer for everything; `git` only for
`verify --tree/--staged/--since`; a scheduler (cron or the application's own)
only for automatic intake. The tool never needs network access.

## 11. Module map

```
bin/rungs                 launcher: adds src/ to sys.path, runs rungs.cli.main
src/rungs/__init__.py     version
src/rungs/miniyaml.py     strict YAML subset: parse and dump (front matter, rungs.yaml)
src/rungs/ticket.py       vocabularies, Ticket dataclass, body sections, atomic io, claim CAS, cycles
src/rungs/roster.py       rungs.yaml model, agent lookup, policy, skill ranks
src/rungs/store.py        locate the store, iterate/find tickets, workable set, ordering, events
src/rungs/scope.py        matching contract, overlap, git change sets, verify
src/rungs/cli.py          argparse wiring; registers commands from the modules below
src/rungs/commands.py     the ticket lifecycle and listing commands
src/rungs/queries.py      the reading commands: list, next, board, show, search, deps, log, stale, triage, agents, whoami
src/rungs/checks.py       verify and doctor
src/rungs/views.py        table, board, JSON rendering
src/rungs/plans.py        plan files, grammar, cut
src/rungs/intake.py       inbox JSON and arbite-md intake, result files
src/rungs/importer.py     import-arbite
src/rungs/docs.py         AGENTS.md and agent file rendering
src/rungs/deploy.py       init and deploy
tests/                    unittest, run with `python3 -m unittest discover -s tests`
```

Each command module exposes `register(subparsers, ctx)`; `cli.py` calls them in
order so modules never edit each other.

## 12. Security notes

- Ticket and intake text is data. Nothing in a ticket is executed. Quoted user
  text is labelled as such in the file.
- Every piece of text written into a body goes through one cleaner: control
  characters removed, runs of three dashes broken, `#`/`##` heading lines
  outside a code fence escaped (so text can never forge a section such as
  Evidence, Review or Notes), unbalanced fences closed (so pasted text can
  never swallow the sections after it).
- Fields read back from ticket files are not trusted either: an agent id is
  validated before it names a file, a `closed` value must look like a date
  before it names a folder, an unparsable scope entry matches nothing.
- The parser caps nesting depth; an unreadable ticket file is skipped with a
  warning by the reading commands and reported by `doctor`, never fatal to
  the whole store.
- `verify --since` accepts only ref-like text and passes it after
  `--end-of-options`, so a ref can never become a git option.
- Files the tool writes get the mode a normal create would (umask), so a
  result file is readable by the producer and tickets stay shared.
- The front matter parser accepts only the documented subset; unknown
  constructs are an error, not a guess.
- No network access anywhere in the tool.
- `verify` and `import-arbite` only read the git tree and the old store.
- Agent ids and ticket ids are validated before they are used in a path.
- The inbox is the only folder an outside system writes to; the tool creates
  ticket files exclusively and never overwrites an existing one.
