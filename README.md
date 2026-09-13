# rungs

File-based tickets for a crew of AI agents (and people) of different ability.

A ticket is a markdown file. The folder it sits in is its status. Every ticket
names the lowest **skill level** that can work it, every agent is registered
in a roster with its own level, and `rungs next` only offers an agent the
rungs it can reach. An orchestrator writes one plan file, cuts it into
tickets, and watches the results come back through a review gate. Outside
systems drop requests into an inbox; a scheduler turns them into tickets.

No database, no server, no dependencies: Python 3.9 or newer and a git
repository.

```
very-low ─ low ─ medium ─ high ─ xhigh        the ladder
   │        │       │       │       │
 haiku   sonnet   opus   astra   fable         one possible crew
```

## Why

Delegating software work to several models at once needs four things a plain
to-do list does not give you:

1. **A skill match.** A weak model that picks up a hard ticket wastes a day; a
   strong one that picks up trivia wastes money. The roster gives every agent a
   level and the tool enforces "at or below".
2. **A file-scope contract.** Two executors editing the same file produce
   conflicts nobody asked for. Every ticket lists the paths it may change; a
   claim that overlaps a live ticket is refused; `rungs verify` shows the
   director what was actually touched.
3. **A review gate.** Done is not done until somebody who did not write it
   says so. `submit` carries evidence, `review` accepts or returns.
4. **A way in for the outside world.** An application's "request a change"
   button, a support queue, a cron job: they drop one small JSON file into
   `.rungs/inbox/` and never need to know the ticket format.

`rungs` descends from [arbite](https://github.com/mattaaron79/arbite) and
keeps what worked there: markdown tickets, folder-as-status, atomic writes,
exclusive-create claims, `--json` everywhere, `doctor`. See
[DESIGN.md](DESIGN.md) for what changed and why.

## Install and deploy into a project

Clone once, deploy into each project:

```bash
git clone https://github.com/<you>/rungs ~/rungs

# Put `rungs` on the PATH (~/.local/bin/rungs) and initialise a project,
# registering the crew in the same command:
~/rungs/deploy.sh --into /path/to/project \
    --agent claude.fable.001:xhigh:director \
    --agent openai.astra.001:high \
    --agent claude.opus.001:medium \
    --agent claude.opus.reviewer:high:reviewer \
    --agent claude.sonnet.001:low \
    --agent claude.haiku.001:very-low

# Or vendor the tool into the project (no PATH change):
~/rungs/deploy.sh --into /path/to/project --vendor

# Migrating from arbite? Convert the old store as well:
~/rungs/deploy.sh --into /path/to/project --import-arbite
```

When it exits 0 the project has `rungs.yaml` (roster and policy),
`.rungs/` (the store), `.rungs/AGENTS.md` (the generated guide agents read),
and `rungs doctor` passes. Add the two printed lines to the project's
`CLAUDE.md` or `AGENTS.md` so agents find the guide. Details:
[docs/DEPLOY.md](docs/DEPLOY.md).

`pip install -e ~/rungs` works too, if you prefer a package.

## Five minutes with rungs

```bash
cd /path/to/project
export RUNGS_AGENT=claude.fable.001          # who you are; --agent works too

# The director writes a plan (design + ticket list) and cuts it into tickets.
rungs plan new intake-v2 --title "Intake, second version"
$EDITOR .rungs/plans/intake-v2.md            # see docs/PLANS.md for the grammar
rungs cut intake-v2

# An executor takes the next rung it can reach, works inside the scope,
# and submits with evidence.
rungs next --agent claude.opus.001 --claim
rungs note rg-3f9a1 --agent claude.opus.001 "parser done, tests next"
rungs submit rg-3f9a1 --agent claude.opus.001 \
    --summary "Inbox JSON becomes triage tickets" \
    --files src/rungs/intake.py,tests/test_intake.py \
    --checks "python3 -m unittest discover -s tests: 48 tests OK"

# The director checks the change set and a reviewer decides.
rungs verify rg-3f9a1 --tree
rungs review rg-3f9a1 --agent claude.opus.reviewer --verdict accept
#   ... or: --verdict return --findings "result file lacks 'at'"

# What is going on?
rungs board
rungs stale
rungs log rg-3f9a1
```

## Concepts

**Skill levels.** `very-low`, `low`, `medium`, `high`, `xhigh` (ranks 1–5).
A ticket's `skill` is the lowest level that can work it. An agent claims at or
below its own level. Aliases such as `vlow`, `very-high` or `1`–`5` are
accepted on input.

**Roster.** `rungs.yaml` at the project root lists every agent with a `skill`
and a `role` (`director`, `executor`, `reviewer`, `critic`), an optional
`floor` (the lowest level `next` offers it by default), the policy (whether
review is required, what an overlapping scope does, what happens to an
unregistered agent, when a claim counts as stale) and the intake defaults.
`rungs docs` renders it into `.rungs/AGENTS.md` so every agent can read its
own level.

**Lifecycle.** `triage → open → in_progress → review → closed`, with `blocked`
and `shelved` on the side. Intake lands in `triage`; `ready` promotes it;
`claim` takes it; `submit` sends it to review with evidence; `review` accepts
(closes) or returns (back to the assignee with findings). Only a director may
`close --no-review`, and that is logged. `reopen` clears the old verdict.

**Scope.** `scope` is a list of paths the ticket may change: `src/app/` (a
directory), `src/app/main.py` (a file), `src/app/*.py` (one level),
`src/**/*.py` (every level). A claim whose scope overlaps a ticket that is
`in_progress` or `review` is refused, warned about, or allowed, per policy.
`rungs verify` compares the declared or actual change set with the scope.

**Plans.** A plan file holds the design and a ticket list written as markdown
headings with a bullet list of fields. `rungs cut` validates the whole plan,
creates every ticket, resolves `depends_on` keys to ids, and records the
mapping back in the plan so cutting again only adds new blocks.

**Intake.** Outside systems write JSON to `.rungs/inbox/`; `rungs intake`
validates, sanitises, deduplicates by `source:key`, creates the ticket and
leaves a result file the producer can read. Arbite-format files are accepted
too, so an existing exporter keeps working.

**Events.** Every transition is one JSON line in `.rungs/events.jsonl`, with
the agent, the action and the statuses; `rungs log` reads it and `rungs stale`
uses it.

## Command overview

| Area | Commands |
|---|---|
| Set up | `init`, `deploy`, `docs`, `agents`, `whoami` |
| Create work | `create`, `plan new/list/show`, `cut`, `request`, `intake`, `triage`, `ready`, `import-arbite` |
| Find work | `list`, `next`, `board`, `show`, `search`, `deps`, `log`, `stale` |
| Move work | `claim`, `release`, `reassign`, `note`, `block`, `unblock`, `shelve`, `unshelve` |
| Finish work | `submit`, `review`, `close`, `reopen` |
| Keep it honest | `set`, `depend`, `verify`, `doctor` |

Every command has `--help`; the generated `.rungs/AGENTS.md` carries a
one-line-per-command table for the installed version (kept short so agents
can read it cheaply every session), and [docs/COMMANDS.md](docs/COMMANDS.md)
carries the full reference for this release. Exit codes: `0` ok, `1` error, `2` nothing
matched, `3` problems found (doctor, verify), `4` refused by policy.

## Integrating an application

The application never writes ticket files. It writes one JSON document per
approved request into `.rungs/inbox/` (or calls `rungs intake --file`), and a
scheduler runs `rungs intake` every few minutes:

```json
{"source": "myapp", "key": "CR-42", "title": "Daily log cannot be saved without a photo",
 "goal": "Saving a daily log with no photo shows an error; it should save.",
 "quoted": "I just want to save my log", "requested_by": "user 12", "approved_by": "admin 3"}
```

```
*/5 * * * * cd /path/to/project && rungs intake --quiet
```

The contract, the defaults per source and the result file are in
[docs/INTAKE.md](docs/INTAKE.md). A producer that would rather not write JSON
runs one command instead:

```
rungs request --source myapp --key CR-42 --title "..." --goal "..." --quoted "..."
```

How a request is collected and approved before it reaches rungs (a button in
an application, a support queue, a person deciding) is the application's
business; rungs takes over at the inbox.

## Documentation

- [DESIGN.md](DESIGN.md): the design, the arbite comparison, the contracts.
- [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md): the director's handbook, from design to acceptance.
- [docs/PLANS.md](docs/PLANS.md): the plan file grammar with a full example.
- [docs/INTAKE.md](docs/INTAKE.md): the intake contract for producers.
- [docs/DEPLOY.md](docs/DEPLOY.md): deploy, vendor, migrate, what "deployed" guarantees.
- [docs/COMMANDS.md](docs/COMMANDS.md): the command reference for this release.
- [docs/AGENTS.example.md](docs/AGENTS.example.md): what a project's generated `.rungs/AGENTS.md` looks like.
- [CHANGELOG.md](CHANGELOG.md), [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

```bash
git clone https://github.com/<you>/rungs && cd rungs
python3 -m unittest discover -s tests -v      # the whole suite, no extras needed
python3 bin/rungs --help
```

Standard library only; keep it that way. Python 3.9 is the floor. Pull
requests need tests and a changelog line.

## Licence

MIT. See [LICENSE](LICENSE).
