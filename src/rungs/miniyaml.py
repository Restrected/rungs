"""A strict, dependency-free YAML subset for front matter and rungs.yaml.

Supported, and nothing else:

- mappings (`key: value`, nested by two-space indentation), keys matching
  ``[A-Za-z0-9_.-]+``;
- block lists of scalars (`- item`), flow lists of scalars (`[a, b]`) and the
  empty mapping `{}`;
- scalars: single-quoted (`''` escapes a quote), double-quoted (backslash
  escapes for ``\\ \" \n \t``), plain text, integers, ``true``/``false``,
  ``null``/``~``/empty;
- full-line comments and inline ` # comment` after a plain or flow value.

Anything outside the subset raises :class:`YamlError` instead of guessing.
That is the point: a ticket store must never be corrupted by a value that
looks like markup, and the tool must never depend on a package a fresh host
lacks.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["YamlError", "loads", "dumps", "quote"]


class YamlError(ValueError):
    pass


_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s+(.*))?$")
_INT_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")
# Plain scalars that would be read back as something other than a string, or
# that would confuse a reader, are quoted on write.
_NEEDS_QUOTE_RE = re.compile(r"[:#\[\]{},'\"&*!|>%@`]|^\s|\s$|^-|^\?")
_RESERVED = {"null", "~", "true", "false", "yes", "no", "on", "off", ""}
MAX_DEPTH = 16


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _strip_comment(text: str) -> str:
    """Remove an inline comment from an unquoted value: ` #` to end of line."""
    idx = text.find(" #")
    if text.startswith("#"):
        return ""
    if idx != -1:
        return text[:idx].rstrip()
    return text.rstrip()


def _parse_quoted(text: str) -> Tuple[str, str]:
    """Parse a quoted scalar at the start of text; return (value, rest)."""
    quote = text[0]
    i = 1
    out: List[str] = []
    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), text[i + 1 :]
            out.append(ch)
            i += 1
        else:
            if ch == "\\":
                if i + 1 >= len(text):
                    raise YamlError("dangling backslash in double-quoted string")
                nxt = text[i + 1]
                mapping = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}
                if nxt not in mapping:
                    raise YamlError(f"unsupported escape \\{nxt} in double-quoted string")
                out.append(mapping[nxt])
                i += 2
                continue
            if ch == '"':
                return "".join(out), text[i + 1 :]
            out.append(ch)
            i += 1
    raise YamlError("unterminated quoted string")


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    if text[0] in "'\"":
        value, rest = _parse_quoted(text)
        rest = rest.strip()
        if rest and not rest.startswith("#"):
            raise YamlError(f"unexpected text after quoted string: {rest!r}")
        return value
    text = _strip_comment(text)
    if text in ("null", "~", ""):
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if _INT_RE.match(text):
        return int(text)
    return text


def _split_flow_items(inner: str) -> List[str]:
    items: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(inner):
        ch = inner[i]
        if quote:
            buf.append(ch)
            if quote == "'" and ch == "'":
                if i + 1 < len(inner) and inner[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                quote = None
            elif quote == '"' and ch == "\\":
                if i + 1 < len(inner):
                    buf.append(inner[i + 1])
                i += 2
                continue
            elif quote == '"' and ch == '"':
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        elif ch in "[]{}":
            raise YamlError("nested flow collections are not supported")
        else:
            buf.append(ch)
        i += 1
    if quote:
        raise YamlError("unterminated quoted string in flow list")
    tail = "".join(buf)
    if tail.strip() or items:
        items.append(tail)
    return [item for item in items if item.strip() != ""] if not any(i.strip() == "" for i in items[:-1]) else _reject_empty(items)


def _reject_empty(items: List[str]) -> List[str]:
    raise YamlError("empty item in flow list")


def _find_flow_end(text: str) -> int:
    """Index of the ']' that closes a flow list starting at text[0], honouring quotes."""
    quote: Optional[str] = None
    i = 1
    while i < len(text):
        ch = text[i]
        if quote:
            if quote == "'" and ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            elif quote == '"' and ch == "\\":
                i += 2
                continue
            elif quote == '"' and ch == '"':
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "]":
            return i
        i += 1
    raise YamlError("unterminated flow list")


def _parse_value(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("["):
        close = _find_flow_end(stripped)
        rest = stripped[close + 1 :].strip()
        if rest and not rest.startswith("#"):
            raise YamlError(f"unexpected text after flow list: {rest!r}")
        inner = stripped[1:close]
        if inner.strip() == "":
            return []
        return [_parse_scalar(item) for item in _split_flow_items(inner)]
    if stripped.startswith("{"):
        rest = _strip_comment(stripped)
        if rest == "{}":
            return {}
        raise YamlError("flow mappings are not supported (only the empty mapping {})")
    if stripped.startswith(("|", ">")):
        raise YamlError("block scalars (| and >) are not supported; keep values on one line")
    if stripped.startswith("&") or stripped.startswith("*"):
        raise YamlError("anchors and aliases are not supported")
    return _parse_scalar(stripped)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def loads(text: str) -> Dict[str, Any]:
    """Parse the subset into a dict. An empty document is an empty dict."""
    lines: List[Tuple[int, int, str]] = []  # (line_no, indent, content)
    for no, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: _indent_of(raw) + 1] and raw.lstrip(" ").startswith("\t"):
            raise YamlError(f"line {no}: tabs are not allowed for indentation")
        content = raw.rstrip()
        if content.strip() == "" or content.lstrip().startswith("#"):
            continue
        lines.append((no, _indent_of(raw), content.strip()))
    pos = 0
    depth = 0

    def parse_block(indent: int) -> Any:
        nonlocal pos, depth
        if pos >= len(lines):
            return {}
        no, ind, content = lines[pos]
        if depth >= MAX_DEPTH:
            raise YamlError(f"line {no}: nesting deeper than {MAX_DEPTH} levels is not supported")
        if content.startswith("- ") or content == "-":
            return parse_list(ind)
        return parse_mapping(ind)

    def parse_list(indent: int) -> List[Any]:
        nonlocal pos
        items: List[Any] = []
        while pos < len(lines):
            no, ind, content = lines[pos]
            if ind < indent:
                break
            if ind > indent:
                raise YamlError(f"line {no}: unexpected indentation")
            if not (content.startswith("- ") or content == "-"):
                raise YamlError(f"line {no}: expected a list item")
            item = content[1:].strip()
            if _KEY_RE.match(item) and not item.startswith(("'", '"')):
                raise YamlError(f"line {no}: lists of mappings are not supported")
            items.append(_parse_value(item))
            pos += 1
        return items

    def parse_mapping(indent: int) -> Dict[str, Any]:
        nonlocal pos, depth
        result: Dict[str, Any] = {}
        while pos < len(lines):
            no, ind, content = lines[pos]
            if ind < indent:
                break
            if ind > indent:
                raise YamlError(f"line {no}: unexpected indentation")
            match = _KEY_RE.match(content)
            if not match:
                if content.startswith("- "):
                    raise YamlError(f"line {no}: a list is not allowed here")
                raise YamlError(f"line {no}: expected 'key: value', got {content!r}")
            key, rest = match.group(1), match.group(2)
            if key in result:
                raise YamlError(f"line {no}: duplicate key {key!r}")
            pos += 1
            if rest is None or _strip_comment(rest) == "" and not rest.strip().startswith(("'", '"', "[")):
                # Nested block or an explicit empty value.
                if pos < len(lines) and lines[pos][1] > ind:
                    depth += 1
                    try:
                        result[key] = parse_block(lines[pos][1])
                    finally:
                        depth -= 1
                else:
                    result[key] = None
            else:
                result[key] = _parse_value(rest)
                if pos < len(lines) and lines[pos][1] > ind:
                    raise YamlError(f"line {lines[pos][0]}: unexpected indentation")
        return result

    data = parse_block(lines[0][1] if lines else 0)
    if pos < len(lines):
        raise YamlError(f"line {lines[pos][0]}: could not parse {lines[pos][2]!r}")
    if not isinstance(data, dict):
        raise YamlError("the document must be a mapping")
    return data


# --------------------------------------------------------------------------
# Dumping
# --------------------------------------------------------------------------


def quote(value: str) -> str:
    """Single-quote a string the way the dumper does."""
    return "'" + value.replace("'", "''") + "'"


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise YamlError(f"cannot dump a value of type {type(value).__name__}")
    if "\n" in value or "\r" in value:
        raise YamlError("scalars must be a single line")
    if value in _RESERVED or _INT_RE.match(value) or _NEEDS_QUOTE_RE.search(value) or "---" in value:
        return quote(value)
    return value


def dumps(data: Dict[str, Any], indent: int = 0) -> str:
    """Dump a mapping in the subset, deterministically, keys in given order."""
    lines: List[str] = []
    pad = " " * indent
    for key, value in data.items():
        if not _KEY_RE.match(f"{key}:"):
            raise YamlError(f"invalid key {key!r}")
        if isinstance(value, dict):
            if not value:
                lines.append(f"{pad}{key}: {{}}")
                continue
            lines.append(f"{pad}{key}:")
            lines.append(dumps(value, indent + 2).rstrip("\n"))
        elif isinstance(value, (list, tuple)):
            items = ", ".join(_dump_scalar(item) for item in value)
            lines.append(f"{pad}{key}: [{items}]")
        else:
            lines.append(f"{pad}{key}: {_dump_scalar(value)}")
    return "\n".join(lines) + "\n"
