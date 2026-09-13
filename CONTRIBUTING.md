# Contributing to rungs

Thank you for helping. The bar is simple: keep the tool small, dependency-free
and predictable for an agent that reads `.rungs/AGENTS.md` cold.

## Ground rules

- **Standard library only.** No runtime dependencies, ever. Python 3.9 is the
  floor; do not use syntax or library features newer than that.
- **The folder is the truth.** Every command that changes a ticket's status
  moves the file and rewrites the front matter in the same operation. Never
  add a code path that leaves the two disagreeing.
- **Text is data.** Nothing in a ticket, a plan or an intake file is ever
  executed or interpreted as a command. Sanitise what comes from outside.
- **Refuse, do not guess.** The YAML subset, the plan grammar and the intake
  contract error out on anything they do not understand.
- **Every mutation is attributed.** New state-changing commands take
  `--agent`, write a note, and log an event.

## Workflow

1. Open an issue or a short design note for anything beyond a bug fix. The
   contracts live in [DESIGN.md](DESIGN.md); change the document in the same
   pull request as the code.
2. Add tests under `tests/` (unittest, no extras). Run the suite:

   ```bash
   python3 -m unittest discover -s tests -v
   ```

3. Regenerate the command reference when a command or its help text changes:

   ```bash
   python3 scripts/render-docs.py
   ```

4. Add a line to [CHANGELOG.md](CHANGELOG.md) under "Unreleased".
5. Keep modules under about 900 lines; split rather than grow.

## Reporting a bug

Include the `rungs --version`, the command, the exit code, and, when a ticket
is involved, the ticket file with anything private removed. `rungs doctor
--json` output helps when the store itself looks wrong.
