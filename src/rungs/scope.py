"""File scope: the matching contract, overlap between tickets, and verify.

Scope entries are paths relative to the project root:

- an entry ending in ``/`` is a directory prefix and covers everything beneath;
- an entry without wildcards is one literal file;
- an entry with ``*``, ``?`` or ``[...]`` is a glob where ``*`` and ``?`` never
  cross a ``/`` and ``**`` matches any number of directories.

See DESIGN.md section 6 for the overlap table this module implements.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .ticket import LIVE_STATUSES, Ticket

__all__ = [
    "ScopeError", "normalise", "classify", "covers", "scope_covers", "overlaps",
    "scope_overlap", "overlapping_tickets", "git_changed", "VerifyResult", "verify",
    "IGNORED_PREFIXES",
]

IGNORED_PREFIXES = (".rungs/", "rungs.yaml", ".rungs.yaml")
_WILDCARDS = set("*?[")


class ScopeError(ValueError):
    pass


def normalise(entry: str) -> str:
    """Canonical form: forward slashes, no leading './' or '/', no '..'."""
    text = str(entry).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = re.sub(r"/{2,}", "/", text)
    if text.startswith("/"):
        raise ScopeError(f"scope entry {entry!r} must be relative to the project root")
    if ".." in text.split("/"):
        raise ScopeError(f"scope entry {entry!r} must not contain '..'")
    if text in ("", "."):
        raise ScopeError("scope entry is empty")
    return text


def classify(entry: str) -> str:
    entry = normalise(entry)
    if any(ch in _WILDCARDS for ch in entry):
        return "glob"
    if entry.endswith("/"):
        return "dir"
    return "literal"


def _glob_regex(pattern: str) -> "re.Pattern":
    out: List[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                # '**/' matches zero or more directories; a trailing '**' matches everything.
                if pattern[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                out.append(re.escape(ch))
            else:
                body = pattern[i + 1 : close]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = close
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def literal_prefix(entry: str) -> str:
    """The text of a glob before its first wildcard."""
    for idx, ch in enumerate(entry):
        if ch in _WILDCARDS:
            return entry[:idx]
    return entry


def covers(entry: str, path: str) -> bool:
    """Does one scope entry cover one concrete file path? An entry or path
    that cannot be normalised covers nothing (doctor reports it)."""
    try:
        entry = normalise(entry)
        path = normalise(path)
    except ScopeError:
        return False
    kind = classify(entry)
    if kind == "dir":
        return path.startswith(entry)
    if kind == "literal":
        return path == entry
    return bool(_glob_regex(entry).match(path))


def scope_covers(scope: Sequence[str], path: str) -> bool:
    return any(covers(entry, path) for entry in scope)


def _prefix_comparable(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a)


def overlaps(a: str, b: str) -> bool:
    """Could two entries cover a common path? Exact for literal/literal and
    literal/glob; conservative (literal prefixes) for glob/glob. An entry that
    cannot be normalised overlaps nothing (doctor reports it)."""
    try:
        a = normalise(a)
        b = normalise(b)
    except ScopeError:
        return False
    ka, kb = classify(a), classify(b)
    if ka == "glob" and kb == "glob":
        return _prefix_comparable(literal_prefix(a), literal_prefix(b))
    if ka == "glob" or kb == "glob":
        glob, other = (a, b) if ka == "glob" else (b, a)
        if classify(other) == "literal":
            return covers(glob, other)
        # A directory prefix against a glob: overlap when the glob's literal
        # prefix sits inside the directory or the directory sits inside the
        # glob's literal prefix.
        return _prefix_comparable(literal_prefix(glob), other)
    if ka == "dir" or kb == "dir":
        return _prefix_comparable(a, b)
    return a == b


def scope_overlap(scope_a: Sequence[str], scope_b: Sequence[str]) -> List[Tuple[str, str]]:
    pairs = []
    for a in scope_a:
        for b in scope_b:
            if overlaps(a, b):
                pairs.append((a, b))
    return pairs


def overlapping_tickets(
    ticket: Ticket, others: Iterable[Ticket], statuses: Optional[set] = None
) -> List[Tuple[Ticket, List[Tuple[str, str]]]]:
    """Every other ticket (in a live status) whose scope overlaps this one's."""
    statuses = statuses or LIVE_STATUSES
    result = []
    for other in others:
        if other.id == ticket.id or other.status not in statuses:
            continue
        pairs = scope_overlap(ticket.scope, other.scope)
        if pairs:
            result.append((other, pairs))
    return result


# --------------------------------------------------------------------------
# Change sets
# --------------------------------------------------------------------------


def _git(project_root: Path, *args: str) -> List[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *args],
            check=False, capture_output=True, text=True,
        )
    except OSError as exc:
        raise ScopeError(f"git is not available: {exc}")
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"git {' '.join(args)} failed"
        raise ScopeError(message)
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def git_changed(
    project_root: Path, tree: bool = False, staged: bool = False, since: Optional[str] = None
) -> List[str]:
    """Changed paths relative to the project root, from the parts asked for."""
    paths: set = set()
    if tree:
        paths.update(_git(project_root, "diff", "--name-only"))
        paths.update(_git(project_root, "ls-files", "--others", "--exclude-standard"))
    if staged:
        paths.update(_git(project_root, "diff", "--name-only", "--cached"))
    if since:
        if since.startswith("-") or not re.match(r"^[A-Za-z0-9_./~^@{}-]+$", since):
            raise ScopeError(f"--since must be a git ref, got {since!r}")
        paths.update(_git(project_root, "diff", "--name-only", "--end-of-options", since, "HEAD"))
    return sorted(paths)


def ignored(path: str) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in IGNORED_PREFIXES)


@dataclass
class VerifyResult:
    ticket: str
    scope: List[str]
    checked: List[str] = field(default_factory=list)
    outside: List[str] = field(default_factory=list)
    declared_not_changed: List[str] = field(default_factory=list)
    sources: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.outside

    def to_dict(self) -> dict:
        return {
            "ticket": self.ticket, "scope": self.scope, "checked": self.checked,
            "outside": self.outside, "declared_not_changed": self.declared_not_changed,
            "sources": self.sources, "ok": self.ok,
        }


def verify(
    ticket: Ticket,
    declared: Optional[Sequence[str]] = None,
    tree_files: Optional[Sequence[str]] = None,
    explicit: Optional[Sequence[str]] = None,
) -> VerifyResult:
    """Judge a change set against the ticket's scope. `declared` is what the
    executor listed at submit, `tree_files` what git reports, `explicit` a
    list given on the command line."""
    result = VerifyResult(ticket=ticket.id, scope=list(ticket.scope))
    groups = {"declared": list(declared or []), "tree": list(tree_files or []), "explicit": list(explicit or [])}
    seen: set = set()
    for name, paths in groups.items():
        kept = []
        for raw in paths:
            try:
                path = normalise(raw)
            except ScopeError:
                continue
            if ignored(path):
                continue
            kept.append(path)
            if path in seen:
                continue
            seen.add(path)
            result.checked.append(path)
            if not scope_covers(ticket.scope, path):
                result.outside.append(path)
        if kept:
            result.sources[name] = kept
    if tree_files is not None and declared:
        tree_set = set(result.sources.get("tree", []))
        result.declared_not_changed = [p for p in result.sources.get("declared", []) if p not in tree_set]
    return result
