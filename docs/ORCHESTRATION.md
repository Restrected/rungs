# Orchestrating a crew with rungs

This is the director's handbook: how one orchestrating agent (or person)
designs a piece of work, cuts it into tickets, hands them to agents of
different ability, watches what comes back, and accepts or returns it. The
commands are the ones in `.rungs/AGENTS.md`; this page is about the method.

## The roles

| Role | What it does | Typical level |
|---|---|---|
| director | Decides architecture and scope, writes plans, cuts tickets, reviews evidence, closes. Never delegates a decision. | xhigh |
| executor | Works one ticket at a time inside its scope, submits with evidence. | very-low to high |
| reviewer | Reads a submitted ticket and its change set; accepts or returns with findings. Never the author. | high |
| critic | Read-only: critiques a plan before it is cut. | high |

The roster in `rungs.yaml` says who is what. A crew of six is a good size:
one director, one reviewer, one critic (often the same model as a strong
executor, run read-only), and executors at three or four levels.

## One cycle

```
design ─▶ critique ─▶ cut ─▶ dispatch ─▶ watch ─▶ review ─▶ accept ─▶ record
```

### 1. Design in a plan file

`rungs plan new <slug> --title "..."` gives you a file with the headings
Goal, Architecture, Decisions and Tickets. Write the design first: what must
be true at the end, the shape of the solution, the decisions that are settled.
Then write the ticket list ([PLANS.md](PLANS.md) has the grammar).

Cut tickets by **skill**, not by size. Ask of each block: what is the least
capable agent that would get this right on the first try? A file rename with
tests is `low`; a parser with an error contract is `high`; anything that
touches authorisation, money or data integrity is `high` or `xhigh` and stays
with the director or a strong executor.

Give each block:

- a Goal that states the end state, not the steps;
- Decisions already made, so the executor does not reopen them;
- Constraints (no dependency changes, Python 3.9, no edits outside scope);
- Acceptance criteria a reviewer can check without talking to anyone;
- Evidence required (which commands to run, what to list);
- a `scope` that does not overlap another block that could run at the same
  time. Overlap is fine for blocks with a `depends_on` between them.

### 2. Critique before cutting

A plan that is cut is expensive to change. Ask a critic (read-only) to look
at the plan file for wrong levels, missing dependencies, overlapping scopes
and ambiguous acceptance. Record what you adopted and what you rejected in
the plan's Decisions section. Then `rungs cut <slug>`.

### 3. Dispatch

Each executor runs `rungs next --agent <id> --claim`, which gives it the
highest rung it can reach, most urgent first. If you drive the executors
yourself (a harness that starts each one with a brief), still make them claim
through the tool: the claim is the mutex and the audit trail.

The brief you hand an executor is the ticket. `rungs show <id>` is the whole
brief; do not write a second one that drifts from it.

Run independent tickets in parallel. `claim` refuses overlapping scopes, so
two executors cannot collide by accident; if a claim is refused, the plan had
a hidden overlap and the fix is in the plan, not in `--allow-overlap`.

### 4. Watch

- `rungs board` shows every live ticket by status with its level.
- `rungs stale` lists claims with no activity past the policy threshold. Ask
  before releasing; `release --force` is the director's tool for an executor
  that is gone.
- `rungs log <id>` is the ticket's history; `rungs log` the store's.
- `rungs verify <id> --tree` compares what the working tree changed with the
  ticket's scope. Tree paths may include another agent's concurrent edits;
  the declared `files` from `submit` are the executor's own claim about its
  change set, and the two together tell you whether the scope held.

### 5. Review

A reviewer who did not author the ticket runs `rungs show <id>`, reads the
Evidence, runs `rungs verify <id>`, and reads the diff. Then:

- `rungs review <id> --agent <reviewer> --verdict accept` closes it;
- `rungs review <id> --agent <reviewer> --verdict return --findings "..."`
  sends it back to the assignee with the findings in the Review section; the
  ticket's `review_rounds` counts the loops.

Security-sensitive tickets get a second reviewer pass with a security brief;
put "security review required" in the acceptance criteria so nobody forgets.

### 6. Accept and record

The director closes nothing by hand while `review_required` is on. When a
ticket must be closed without review (a doc typo, a dead ticket), the
director uses `close --no-review --reason "..."`; the bypass is in the notes
and the event log for anyone to see.

Mark the plan `done` (`status: done` in its front matter) when every cut
ticket is closed; `rungs plan show <slug>` lists them with their status.

## Feeding the crew from an application

An application's approved change requests arrive through the inbox
([INTAKE.md](INTAKE.md)). They land in `triage`, because a request written by
a user has no scope, no level and no acceptance criteria. Triage is director
work: read it, set `skill`, `scope`, `acceptance` (`rungs set`), then `rungs
ready <id>`. A request that is really several pieces of work becomes a plan
instead; note the origin ticket in the plan and close it against the plan
when the plan is done.

## What not to do

- Do not let an agent pick its own level. The roster decides; `--force` on a
  claim is for the director and is written into the ticket.
- Do not describe work in a chat message and hope the executor remembers it.
  If it is not in the ticket, it was not asked.
- Do not accept "tests pass" without the command and its summary line in the
  Evidence section.
- Do not widen a scope to `**` to make a refusal go away. Split the ticket.
- Do not edit ticket files by hand except to fill a plan or a triage ticket;
  `doctor` will find drift, but the event log will not know who did it.
