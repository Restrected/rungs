"""`rungs init` and `rungs deploy`.

`init` makes a project ready: the `.rungs/` layout, a `rungs.yaml` roster and
the generated docs. `deploy` is what you run once per project from a clone of
this repository: it puts the command on the PATH (or vendors it into the
project), runs `init` there, optionally imports an old `.arbite/` store, runs
`doctor`, and prints the two lines to add to the project's CLAUDE.md or
AGENTS.md. It exits 0 only when every step succeeded.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import List, Optional

from . import docs
from .roster import Agent, Roster, parse_agent_spec, template
from .store import Store
from .ticket import write_atomic

__all__ = ["checkout_root", "run_init", "register"]

# Where --vendor puts the tool, relative to the project root.
VENDOR_DIR = ("tools", "rungs")
DEFAULT_BIN = "~/.local/bin"

WRAPPER = """#!/usr/bin/env bash
# Vendored rungs launcher. The tool lives beside this file in bin/ and src/;
# it needs nothing installed but Python 3.9 or newer.
exec python3 "$(dirname "$0")/bin/rungs" "$@"
"""

PROJECT_LINES = [
    "This project tracks work in rungs (file-based tickets under .rungs/). "
    "Read .rungs/AGENTS.md before doing anything else.",
    "Your agent id, skill level and role are in rungs.yaml; take work with "
    "`rungs next --agent <your id> --claim` and never work outside a ticket's scope.",
]


def checkout_root() -> Path:
    """The root of this checkout: src/rungs/deploy.py -> src/rungs -> src -> root."""
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------


def run_init(
    ctx,
    root: Path,
    project: Optional[str] = None,
    specs: Optional[List[str]] = None,
    force_docs: bool = False,
) -> dict:
    """Create the store, the roster and the docs in `root`. Idempotent."""
    from .cli import RungsError

    root = root.resolve()
    if root.exists() and not root.is_dir():
        raise RungsError("{0} is not a directory".format(root))
    root.mkdir(parents=True, exist_ok=True)

    store = Store(root / ".rungs")
    created_folders = [str(p) for p in store.ensure_layout()]
    ctx.use_store(store)

    agents: List[Agent] = [parse_agent_spec(spec) for spec in (specs or [])]
    config = Roster.find_path(root)
    created_config = False
    updated_config = False
    if config is None:
        config = root / "rungs.yaml"
        write_atomic(template(project, agents), config)
        created_config = True
    else:
        roster = Roster.load(root)
        changed = False
        if project and roster.project != project:
            roster.project = project
            changed = True
        for agent in agents:
            if roster.add_agent(agent):
                changed = True
        if changed:
            ctx.warn(
                "rewriting {0}: comments and key order in it are not preserved".format(config.name)
            )
            write_atomic(roster.dumps(), config)
            updated_config = True

    roster = ctx.reload_roster()
    written = docs.write_docs(store, roster, ctx, force_agent_files=force_docs)
    return {
        "project_root": str(root),
        "store": str(store.root),
        "created_folders": created_folders,
        "config": str(config),
        "created_config": created_config,
        "updated_config": updated_config,
        "docs": [str(p) for p in written],
        "agents": sorted(roster.agents),
    }


def _report_init(ctx, result: dict) -> None:
    root = Path(result["project_root"])

    def shown(path) -> str:
        try:
            return str(Path(path).relative_to(root))
        except ValueError:
            return str(path)

    if result["created_folders"]:
        ctx.out("created {0}/ with {1} folder(s)".format(
            shown(result["store"]), len(result["created_folders"])))
    else:
        ctx.out("{0}/ is already in place".format(shown(result["store"])))
    if result["created_config"]:
        ctx.out("created {0}".format(shown(result["config"])))
    elif result["updated_config"]:
        ctx.out("updated {0}".format(shown(result["config"])))
    else:
        ctx.out("{0} is unchanged".format(shown(result["config"])))
    ctx.out("wrote {0} and {1} agent file(s)".format(
        shown(result["docs"][0]), max(0, len(result["docs"]) - 1)))
    if not result["agents"]:
        ctx.out(
            "no agents are registered yet: add them to {0} (or rerun with --agent "
            "id:skill[:role[:floor]]) before any work can be claimed".format(shown(result["config"]))
        )
    else:
        ctx.out("agents: {0}".format(", ".join(result["agents"])))


def _init(args, ctx) -> Optional[int]:
    result = run_init(
        ctx, ctx.cwd, project=args.project, specs=args.agent or [],
        force_docs=bool(args.force_docs),
    )
    if ctx.json_output:
        ctx.emit_json(result)
    else:
        _report_init(ctx, result)
    return 0


# --------------------------------------------------------------------------
# deploy: step 1, the command itself
# --------------------------------------------------------------------------


def _on_path(directory: Path) -> bool:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).expanduser().resolve() == directory.resolve():
                return True
        except OSError:  # pragma: no cover - an unreadable PATH entry
            continue
    return False


def _link_onto_path(ctx, checkout: Path, bin_dir: Path) -> str:
    from .cli import RungsError

    target = checkout / "bin" / "rungs"
    if not target.is_file():
        raise RungsError("this checkout has no bin/rungs at {0}".format(target))
    bin_dir = bin_dir.expanduser()
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "rungs"
    if link.is_symlink():
        current = os.readlink(str(link))
        if Path(current) == target or (link.exists() and link.resolve() == target.resolve()):
            message = "rungs already links to {0}".format(target)
        else:
            link.unlink()
            link.symlink_to(target)
            message = "replaced the rungs symlink (it pointed at {0}) with {1}".format(
                current, target)
    elif link.exists():
        raise RungsError(
            "{0} exists and is not a symlink; move it aside or choose another --bin "
            "directory (nothing was overwritten)".format(link)
        )
    else:
        link.symlink_to(target)
        message = "linked {0} -> {1}".format(link, target)
    if not _on_path(bin_dir):
        ctx.warn("{0} is not on your PATH; add it or call the tool by its full path".format(bin_dir))
    return message


def _tree_files(root: Path) -> set:
    """Every file under `root`, relative, ignoring compiled leftovers."""
    found = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            if path.is_symlink():
                found.add(path.relative_to(root))
            continue
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        found.add(path.relative_to(root))
    return found


def _unexpected_in(target: Path, source: Path) -> List[str]:
    """Files under an existing vendored tree that this checkout would not have
    put there. Vendoring replaces the tree wholesale, so anything here would be
    destroyed without being asked about."""
    if not target.is_dir():
        return []
    return sorted(str(p) for p in _tree_files(target) - _tree_files(source))


def _vendor(ctx, checkout: Path, into: Path, force: bool = False) -> str:
    from .cli import RungsError

    source_bin = checkout / "bin"
    source_src = checkout / "src" / "rungs"
    if not source_bin.is_dir() or not source_src.is_dir():
        raise RungsError("this checkout has no bin/ and src/rungs/ to vendor ({0})".format(checkout))
    dest = into.joinpath(*VENDOR_DIR)
    pairs = ((source_bin, dest / "bin"), (source_src, dest / "src" / "rungs"))
    if not force:
        strangers = []
        for source, target in pairs:
            strangers += [
                "{0}/{1}".format(target, name) for name in _unexpected_in(target, source)
            ]
        if strangers:
            shown = ", ".join(strangers[:5]) + (" and more" if len(strangers) > 5 else "")
            raise RungsError(
                "{0} already holds {1} file(s) this checkout did not put there ({2}); vendoring "
                "replaces those trees wholesale, so nothing was touched. Move them aside, or pass "
                "--force to overwrite.".format(dest, len(strangers), shown)
            )
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for source, target in pairs:
        if target.exists():
            shutil.rmtree(str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(source), str(target), ignore=ignore)
    launcher = dest / "bin" / "rungs"
    if launcher.is_file():
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    wrapper = dest / "rungs"
    write_atomic(WRAPPER, wrapper)
    wrapper.chmod(0o755)
    return "vendored the tool into {0} (run it as {1})".format(dest, wrapper)


# --------------------------------------------------------------------------
# deploy
# --------------------------------------------------------------------------


def _run_doctor(ctx) -> Optional[bool]:
    """True when doctor passed, False when it failed, None when the command is
    not registered yet."""
    from .cli import RungsError

    parser = (getattr(ctx, "subparsers", None) or {}).get("doctor")
    if parser is None:
        return None
    try:
        namespace = parser.parse_args([])
    except SystemExit:  # pragma: no cover - doctor takes no required arguments
        return None
    handler = getattr(namespace, "handler", None)
    if handler is None:  # pragma: no cover
        return None
    try:
        code = handler(namespace, ctx)
    except RungsError as exc:
        ctx.warn("doctor: {0}".format(exc))
        return False
    return (code or 0) == 0


def _deploy(args, ctx) -> Optional[int]:
    from .cli import RungsError
    from . import importer

    checkout = checkout_root()
    into = Path(args.into).expanduser() if args.into else ctx.cwd
    if not into.is_absolute():
        into = (ctx.cwd / into).resolve()
    into = into.resolve() if into.exists() else into
    failures: List[str] = []

    ctx.out("rungs deploy: {0} -> {1}".format(checkout, into))

    # 1. the command resolves.
    if args.vendor:
        ctx.out("1. " + _vendor(ctx, checkout, into, force=bool(args.force)))
    else:
        bin_dir = Path(args.bin).expanduser() if args.bin else Path(DEFAULT_BIN).expanduser()
        ctx.out("1. " + _link_onto_path(ctx, checkout, bin_dir))

    # 2-4. the store, the roster and the docs. run_init hands the new store to
    # the context, so everything after this step works on the deployed project.
    ctx.cwd = into
    result = run_init(ctx, into, project=args.project, specs=args.agent or [])
    ctx.out("2. store: {0}".format(result["store"]))
    ctx.out("3. roster: {0}{1}".format(
        result["config"],
        " (created)" if result["created_config"] else
        (" (updated)" if result["updated_config"] else " (unchanged)"),
    ))
    ctx.out("4. docs: {0} and {1} agent file(s)".format(
        result["docs"][0], max(0, len(result["docs"]) - 1)))
    if not result["agents"]:
        ctx.out("   no agents yet: add them to {0} before any work can be claimed".format(
            result["config"]))

    # 5. the old store.
    if args.import_arbite is None:
        ctx.out("5. no arbite store to import (pass --import-arbite [PATH] to convert one)")
    else:
        source = Path(args.import_arbite).expanduser() if args.import_arbite else into / ".arbite"
        if not source.is_absolute():
            source = (into / source).resolve()
        try:
            summary = importer.run_import(ctx, ctx.store, ctx.roster, source)
            ctx.out("5. imported {0} ticket(s) from {1}".format(
                len(summary["imported"]), summary["source"]))
            for warning in summary["warnings"]:
                ctx.warn("import: {0}".format(warning))
        except RungsError as exc:
            failures.append("import: {0}".format(exc))
            ctx.warn("import: {0}".format(exc))

    # 6. doctor.
    if args.no_doctor:
        ctx.out("6. doctor skipped (--no-doctor)")
    else:
        outcome = _run_doctor(ctx)
        if outcome is None:
            ctx.out("6. doctor is not registered in this build; skipped")
        elif outcome:
            ctx.out("6. doctor passed")
        else:
            failures.append("doctor reported problems")
            ctx.out("6. doctor reported problems (run `rungs doctor --fix`)")

    # 7. what to put in the project's own instructions.
    ctx.out("")
    ctx.out("7. add these two lines to {0}/CLAUDE.md or {0}/AGENTS.md:".format(into))
    for line in PROJECT_LINES:
        ctx.out("   " + line)

    if failures:
        ctx.warn("deploy did not finish cleanly: {0}".format("; ".join(failures)))
        return 1
    return 0


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def _add_agent_specs(parser) -> None:
    parser.add_argument(
        "--agent", metavar="ID:SKILL[:ROLE[:FLOOR]]", action="append", default=[],
        help=(
            "register an agent, e.g. claude.opus.001:medium or "
            "claude.fable.001:xhigh:director:high. Repeatable; an agent already in "
            "rungs.yaml is updated, never duplicated."
        ),
    )


def register(subparsers, ctx) -> None:
    init_parser = ctx.command(
        subparsers, "init", _init,
        help="create .rungs/, rungs.yaml and the generated docs in this directory",
        description=(
            "Prepare the current directory (or -C DIR) for rungs: create every folder of the\n"
            ".rungs/ layout, write rungs.yaml if it is missing, add any --agent given to it, and\n"
            "render .rungs/AGENTS.md and the agent files. Safe to run again: nothing already in\n"
            "place is destroyed. rungs.yaml is only rewritten when an --agent or --project\n"
            "actually changes it, and a rewrite does not preserve comments."
        ),
    )
    init_parser.add_argument("--project", metavar="NAME", help="project name written into rungs.yaml")
    _add_agent_specs(init_parser)
    init_parser.add_argument(
        "--force-docs", action="store_true",
        help="rewrite the agent files completely, discarding the part below the generated header",
    )
    ctx.add_json_option(init_parser)

    deploy_parser = ctx.command(
        subparsers, "deploy", _deploy,
        help="put rungs on the PATH (or vendor it) and initialise a project",
        description=(
            "Deploy this checkout into a project. In order: (1) link <bin>/rungs to this\n"
            "checkout's bin/rungs, or with --vendor copy bin/ and src/rungs/ into\n"
            "<project>/tools/rungs/ with a wrapper; (2) create <project>/.rungs/; (3) create or\n"
            "update <project>/rungs.yaml with the --agent entries; (4) write .rungs/AGENTS.md and\n"
            "the agent files; (5) import an .arbite/ store when asked; (6) run doctor; (7) print\n"
            "the two lines to add to the project's CLAUDE.md or AGENTS.md. Exits 0 only when\n"
            "every step succeeded. An existing regular file at <bin>/rungs is never overwritten."
        ),
    )
    deploy_parser.add_argument(
        "--into", metavar="DIR", help="the project to deploy into (default: this directory)")
    where = deploy_parser.add_mutually_exclusive_group()
    where.add_argument(
        "--bin", metavar="DIR",
        help="directory for the rungs symlink (default: {0})".format(DEFAULT_BIN))
    where.add_argument(
        "--vendor", action="store_true",
        help="copy the tool into <project>/tools/rungs/ instead of linking it onto the PATH")
    deploy_parser.add_argument(
        "--project", metavar="NAME", help="project name written into rungs.yaml")
    _add_agent_specs(deploy_parser)
    deploy_parser.add_argument(
        "--import-arbite", metavar="PATH", nargs="?", const="", default=None,
        help="also convert an arbite store (default: <project>/.arbite)")
    deploy_parser.add_argument(
        "--no-doctor", action="store_true", help="skip the doctor check at the end")
    deploy_parser.add_argument(
        "--force", action="store_true",
        help="with --vendor, replace <project>/tools/rungs/bin and src/rungs even when they "
             "hold files this checkout did not put there. Never overwrites a regular file at "
             "<bin>/rungs: that is always refused")
