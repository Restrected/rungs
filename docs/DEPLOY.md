# Deploying rungs into a project

Clone the repository once, anywhere:

```
git clone <url> ~/rungs
```

Then run one command per project. `deploy.sh` is a thin wrapper over
`bin/rungs deploy`, so the logic lives in one place and both take the same
options.

```
~/rungs/deploy.sh --into /path/to/project \
    --agent claude.fable.001:xhigh:director \
    --agent claude.opus.001:medium \
    --agent claude.opus.reviewer:high:reviewer
```

Prerequisites: Python 3.9 or newer, and nothing else. `git` is needed only for
`rungs verify --tree/--staged/--since`; a scheduler (cron, systemd, or the
application's own) only for automatic intake. The tool never touches the
network and installs no packages.

## What "deployed" means

`rungs deploy` runs seven steps in order and exits 0 **only when every one of
them succeeded**. When it exits 0, all of the following are true:

1. **`rungs` resolves.** Either `<bin>/rungs` (default `~/.local/bin/rungs`) is
   a symlink to this clone's `bin/rungs`, or — with `--vendor` — the tool sits
   under `<project>/tools/rungs/` with its own wrapper. The command warns on
   stderr when the bin directory is not on your `PATH`.
2. **`<project>/.rungs/` exists** with every folder of the layout: `triage/`,
   `open/`, `in_progress/`, `review/`, `blocked/`, `shelved/`, `closed/`,
   `inbox/`, `inbox/done/`, `plans/`, `agents/`.
3. **`<project>/rungs.yaml` exists.** Every `--agent` you passed is in it,
   added to an existing file and never duplicated (passing an agent that is
   already there with different settings updates it in place). With no
   `--agent`, a template with commented examples is written and the command
   says agents must be added before any work can be claimed.
4. **`.rungs/AGENTS.md` and the agent files are written**, rendered from the
   roster and from the tool's own command reference.
5. **An `.arbite/` store was imported** if you asked for one.
6. **`rungs doctor` passed.**
7. **The two lines to add to the project's `CLAUDE.md` or `AGENTS.md` were
   printed.**

If a step fails, the command says which, and exits 1. Nothing is half-done
silently.

## Options

```
rungs deploy [--into DIR] [--bin DIR | --vendor] [--project NAME]
             [--agent ID:SKILL[:ROLE[:FLOOR]]]... [--import-arbite [PATH]] [--no-doctor]
```

| Option | Meaning |
|---|---|
| `--into DIR` | The project to deploy into. Default: the current directory. |
| `--bin DIR` | Where to put the `rungs` symlink. Default `~/.local/bin`. |
| `--vendor` | Copy the tool into the project instead of linking it. Mutually exclusive with `--bin`. |
| `--project NAME` | Project name written into `rungs.yaml`. |
| `--agent SPEC` | `id:skill[:role[:floor]]`, repeatable. Role is `director`, `executor`, `reviewer` or `critic` (default `executor`). Floor is the lowest rung `rungs next` offers that agent by default. |
| `--import-arbite [PATH]` | Also convert an arbite store. Default path `<project>/.arbite`. |
| `--no-doctor` | Skip step 6. |
| `--force` | With `--vendor`, replace the vendored trees even when they hold files this checkout did not put there. Never overrides the refusal to overwrite a regular file at `<bin>/rungs`. |

Agent ids are `company.model.instance` with lower-case letters, digits, dots
and dashes. An id with a `/` in it is refused when it is read, not after it
has corrupted a path.

## Link mode (the default)

```
~/rungs/deploy.sh --into /path/to/project --agent claude.opus.001:medium
```

`<bin>/rungs` becomes a symlink to `~/rungs/bin/rungs`. Updating the tool is
then `git pull` in the clone; every project follows at once.

- An existing symlink that already points at this clone is left alone.
- An existing symlink that points somewhere else (including a broken one) is
  replaced, and the command says what it replaced.
- **An existing regular file is never overwritten.** The command refuses, says
  so, and exits 1 without touching anything else. Move the file aside or pass
  another `--bin`.

If `<bin>` is not on your `PATH`, the command warns. Add it (`export
PATH="$HOME/.local/bin:$PATH"`) or call the tool by its full path.

## Vendor mode

```
~/rungs/deploy.sh --into /path/to/project --vendor
```

Copies `bin/` and `src/rungs/` into `<project>/tools/rungs/` (no
`__pycache__`, no `.pyc`) and writes an executable wrapper at
`<project>/tools/rungs/rungs`:

```bash
#!/usr/bin/env bash
exec python3 "$(dirname "$0")/bin/rungs" "$@"
```

Run it as `tools/rungs/rungs …`, or add `tools/rungs` to your `PATH` inside
the project.

Use vendor mode when the tool must travel with the repository: a CI job, a
container, a machine where nobody may write to `~/.local/bin`, or a project
whose collaborators should not each have to clone something first. The trade
is that updating the tool means re-running `deploy --vendor`, per project.
Re-running it replaces the copied trees wholesale, so a file left over from an
older version does not survive.

Because it replaces them wholesale, `--vendor` first checks what is there.
**If `tools/rungs/bin/` or `tools/rungs/src/rungs/` holds a file this checkout
would not have written — a local patch, a note, anything — the command refuses,
names the files, and touches nothing.** Move them aside, or pass `--force` to
overwrite. Compiled leftovers (`__pycache__`, `*.pyc`) are ignored by the check,
so running the vendored tool never trips it. `--force` applies only to this:
a regular file at `<bin>/rungs` is refused whatever you pass.

Commit `tools/rungs/` if you want the guarantee that a fresh clone of the
project can run the tool with nothing installed.

## Importing an existing arbite store

```
~/rungs/deploy.sh --into /path/to/project --import-arbite
~/rungs/deploy.sh --into /path/to/project --import-arbite /somewhere/.arbite
```

Ids are kept (`tic-xxxx` is a valid rungs id), so nothing that refers to an
old ticket by id goes stale. `tier` becomes `skill`, `type` becomes `kind`,
`raw` becomes `triage`, the `wishlist/` folder becomes `shelved` with a
`wishlist` tag, `## Description` becomes the Goal section and `## Notes` is
kept. An id already in the rungs store is skipped, so importing twice is safe.
The arbite store is only ever read.

See `rungs import-arbite --help` for the full mapping, and run it with
`--dry-run` first to see the table.

## After deploying

Add the two printed lines to the project's `CLAUDE.md` or `AGENTS.md`:

```
This project tracks work in rungs (file-based tickets under .rungs/). Read .rungs/AGENTS.md before doing anything else.
Your agent id, skill level and role are in rungs.yaml; take work with `rungs next --agent <your id> --claim` and never work outside a ticket's scope.
```

Then, if outside systems will feed the inbox, add the scheduler line:

```
*/5 * * * * cd /path/to/project && rungs intake --quiet
```

Commit `rungs.yaml` and `.rungs/` with the project. The tickets are the record
of the work; they belong in the repository with it.

## Doing it again

`rungs deploy` and `rungs init` are both idempotent. Re-run `deploy` to add an
agent, to re-point the symlink after moving the clone, or to refresh the docs
after editing `rungs.yaml` (`rungs docs` alone does the last one). `rungs.yaml`
is rewritten only when something in it actually changes, and a rewrite does not
preserve comments — the command warns before it does.

`rungs init` is the same thing without step 1: use it when the command is
already on the PATH and you only want a store in a new directory.
