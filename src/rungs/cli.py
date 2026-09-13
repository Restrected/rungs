"""Command line entry point and the contract every command module follows.

Each command module exposes ``register(subparsers, ctx)`` and adds its
subcommands with ``ctx.command(subparsers, name, handler, help=..., ...)``.
A handler is ``handler(args, ctx) -> int | None`` and returns the exit code
(None means 0). Handlers raise :class:`Refused` for policy refusals (exit 4)
and :class:`Problems` when a check found integrity or scope problems
(exit 3); ``NoMatch`` means the query ran but matched nothing (exit 2). Any
other :class:`RungsError` (or the store, ticket, roster, scope errors) is an
ordinary error (exit 1) printed as ``rungs: <message>`` on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import __version__
from .miniyaml import YamlError
from .roster import Agent, Roster, RosterError, UnknownAgent, validate_agent_id
from .scope import ScopeError
from .store import Store, StoreError
from .ticket import TicketError

__all__ = [
    "EXIT_OK", "EXIT_ERROR", "EXIT_NO_MATCH", "EXIT_PROBLEMS", "EXIT_REFUSED",
    "RungsError", "Refused", "Problems", "NoMatch", "Context", "build_parser", "main",
]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_MATCH = 2
EXIT_PROBLEMS = 3
EXIT_REFUSED = 4


class RungsError(Exception):
    """An ordinary error: message on stderr, exit 1."""

    exit_code = EXIT_ERROR


class Refused(RungsError):
    """Refused by policy: skill, overlap, reviewer is the author, unregistered agent."""

    exit_code = EXIT_REFUSED


class Problems(RungsError):
    """A check ran and found integrity or scope problems."""

    exit_code = EXIT_PROBLEMS


class NoMatch(RungsError):
    """The query ran fine but matched nothing."""

    exit_code = EXIT_NO_MATCH


Handler = Callable[[argparse.Namespace, "Context"], Optional[int]]


class Context:
    """Shared services for handlers: the store, the roster, the acting agent,
    output helpers. Store and roster are located lazily so `init` and
    `deploy` can run where no store exists yet."""

    def __init__(self, cwd: Optional[Path] = None, stdout=None, stderr=None):
        self.cwd = (cwd or Path.cwd()).resolve()
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._store: Optional[Store] = None
        self._roster: Optional[Roster] = None
        self.json_output = False
        self.quiet = False
        # Set by build_parser so `docs` can render the command reference.
        self.parser: Optional[argparse.ArgumentParser] = None
        self.subparsers: Dict[str, argparse.ArgumentParser] = {}

    # -- services ---------------------------------------------------------

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store.require(self.cwd)
            self._store.on_error = self._unreadable
        return self._store

    def store_or_none(self) -> Optional[Store]:
        if self._store is None:
            self._store = Store.locate(self.cwd)
            if self._store is not None:
                self._store.on_error = self._unreadable
        return self._store

    def _unreadable(self, message: str) -> None:
        self.warn(f"skipping an unreadable ticket file ({message}); run 'rungs doctor'")

    def use_store(self, store: Store) -> None:
        self._store = store
        self._roster = None

    @property
    def roster(self) -> Roster:
        if self._roster is None:
            self._roster = self.store.roster()
        return self._roster

    def reload_roster(self) -> Roster:
        self._roster = None
        return self.roster

    def agent_id(self, args: argparse.Namespace) -> str:
        """The acting agent id from --agent or RUNGS_AGENT; validated."""
        raw = getattr(args, "agent", None) or os.environ.get("RUNGS_AGENT")
        if not raw:
            raise RungsError("an acting agent is required: pass --agent <id> or set RUNGS_AGENT")
        return validate_agent_id(raw)

    def acting_agent(self, args: argparse.Namespace) -> Agent:
        """The registered Agent for --agent. Under policy 'warn' an unknown
        id is warned about and returned as a provisional executor at the
        lowest level; under 'refuse' it raises Refused."""
        agent_id = self.agent_id(args)
        try:
            return self.roster.agent(agent_id)
        except UnknownAgent as exc:
            if self.roster.policy.unregistered_agents == "warn":
                self.warn(f"{exc}; continuing as an unregistered very-low executor")
                return Agent(id=agent_id, skill="very-low", role="executor")
            raise Refused(str(exc))

    # -- output -----------------------------------------------------------

    def out(self, text: str = "") -> None:
        self.stdout.write(text + "\n")

    def warn(self, text: str) -> None:
        self.stderr.write(f"rungs: {text}\n")

    def info(self, text: str) -> None:
        if not self.quiet:
            self.stderr.write(f"rungs: {text}\n")

    def emit_json(self, data: Any) -> None:
        self.stdout.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")

    # -- registration helpers ---------------------------------------------

    @staticmethod
    def command(
        subparsers, name: str, handler: Handler, help: str, description: Optional[str] = None,
        aliases: Optional[list] = None, **kwargs
    ) -> argparse.ArgumentParser:
        parser = subparsers.add_parser(
            name, help=help, description=description or help, aliases=aliases or [],
            formatter_class=argparse.RawDescriptionHelpFormatter, **kwargs
        )
        parser.set_defaults(handler=handler, command_name=name)
        return parser

    @staticmethod
    def add_agent_option(parser: argparse.ArgumentParser, required_note: bool = True) -> None:
        parser.add_argument(
            "--agent", metavar="ID",
            help="acting agent id (company.model.instance); default: $RUNGS_AGENT"
            + (". Required: the event log and the notes say who did what." if required_note else ""),
        )

    @staticmethod
    def add_json_option(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--json", action="store_true", help="machine-readable JSON on stdout")


def _command_modules():
    # Imported here so a broken optional module reports a clean error and the
    # registration order is explicit: lifecycle first, then the rest.
    from . import commands, queries, checks, plans, intake, importer, deploy, docs
    return [commands, queries, checks, plans, intake, importer, deploy, docs]


def build_parser(ctx: Context) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rungs",
        description=(
            "File-based tickets for a crew of agents of different skill levels. "
            "Tickets are rungs on a ladder (very-low, low, medium, high, xhigh); an "
            "agent only takes the rungs it can reach. See .rungs/AGENTS.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"rungs {__version__}")
    parser.add_argument("-C", metavar="DIR", dest="chdir", help="run as if started in DIR")
    subparsers = parser.add_subparsers(dest="command", metavar="command")
    subparsers.required = True
    ctx.parser = parser
    for module in _command_modules():
        module.register(subparsers, ctx)
    ctx.subparsers = dict(subparsers.choices)
    return parser


def main(argv: Optional[list] = None, ctx: Optional[Context] = None) -> int:
    ctx = ctx or Context()
    parser = build_parser(ctx)
    args = parser.parse_args(argv)
    if getattr(args, "chdir", None):
        ctx.cwd = Path(args.chdir).resolve()
        ctx._store = None
        ctx._roster = None
    ctx.json_output = bool(getattr(args, "json", False))
    ctx.quiet = bool(getattr(args, "quiet", False))
    handler: Handler = args.handler
    try:
        code = handler(args, ctx)
        return EXIT_OK if code is None else int(code)
    except RungsError as exc:
        if str(exc):
            ctx.stderr.write(f"rungs: {exc}\n")
        return exc.exit_code
    except (StoreError, TicketError, RosterError, ScopeError, YamlError) as exc:
        ctx.stderr.write(f"rungs: {exc}\n")
        return EXIT_ERROR
    except RecursionError:
        ctx.stderr.write("rungs: a file is nested too deeply to read; run 'rungs doctor'\n")
        return EXIT_ERROR
    except KeyboardInterrupt:
        ctx.stderr.write("rungs: interrupted\n")
        return 130
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
