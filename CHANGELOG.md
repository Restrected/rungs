# Changelog

All notable changes to rungs are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.1] - 2026-09-13

### Changed

- The generated `.rungs/AGENTS.md` now carries a one-line-per-command table
  instead of every command's full `--help` page, cutting the guide agents
  read each session to roughly a third of its size. The full reference
  stays available through `rungs <command> --help` and `docs/COMMANDS.md`.

## [1.0.0] - 2026-09-13

First release. Designed as the successor to arbite for delegating work to a
crew of agents of different skill levels.

### Added

- Five-level skill ladder (`very-low` to `xhigh`) on tickets and a roster
  (`rungs.yaml`) that assigns every agent a level and a role; `next` and
  `claim` enforce "at or below".
- Lifecycle with a review gate: `triage`, `open`, `in_progress`, `review`,
  `blocked`, `shelved`, `closed`; `submit` with evidence, `review` with
  accept/return, director-only `close --no-review`, `reopen` clearing the old
  verdict; a recovery table (`release`, `reassign`, `unblock`, `stale`).
- File scope on every ticket with an explicit matching contract, overlap
  refusal on claim, and `verify` against the declared or actual change set.
- Fixed ticket body sections: Goal, Decisions already made, Constraints,
  Acceptance criteria, Evidence required, Evidence, Review, Notes.
- Plans: one markdown file per design with a ticket list; `cut` validates and
  creates every ticket with dependencies resolved, idempotently.
- Intake: JSON (or arbite-format markdown) files in `.rungs/inbox/` become
  tickets, deduplicated by `source:key`, sanitised, with a result file per
  request.
- `rungs request` to file a request from the command line through the same
  path as the inbox, immediately or deferred to the scheduled intake.
- `import-arbite` to convert an existing `.arbite/` store.
- `deploy.sh` / `rungs deploy`: PATH symlink or vendored copy, store
  initialisation, roster registration from the command line, docs, doctor.
- Generated `.rungs/AGENTS.md` with a quick start, the roster, the rules and
  the command reference; agent files with a generated header.
- Append-only event log (`.rungs/events.jsonl`) and `rungs log`.
- A strict dependency-free YAML subset for front matter and the roster; a
  front matter boundary is only a line that is exactly `---`, so values
  containing three dashes can no longer corrupt the store.

[1.0.0]: https://github.com/<you>/rungs/releases/tag/v1.0.0
