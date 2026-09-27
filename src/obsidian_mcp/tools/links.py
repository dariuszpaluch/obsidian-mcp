"""Obsidian-style link rewriting for moves and renames.

Handles both link styles Obsidian writes - ``[[wikilinks]]`` / ``![[embeds]]``
and ``[text](relative/path.md)`` / ``![alt](image.png)`` markdown links - plus
folder paths quoted in Dataview ``FROM`` queries. Fenced code blocks and inline
code are left alone, the same as Obsidian's own "update internal links".

A link is only rewritten when it would stop resolving to the same file after
the move, so a plain move keeps ``[[Unique Name]]`` untouched and does not
churn files through sync.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})([^\n]*)\n.*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(`[^`\n]+`)")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]*)(#[^\[\]|\n]*)?(\|[^\[\]\n]*)?\]\]")
_MARKDOWN_LINK_RE = re.compile(
    r"(\]\()(<[^>\n]+>|(?:[^()\s]|\([^()\s]*\))+)((?:\s+\"[^\"\n]*\")?\))"
)
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_QUOTED_RE = re.compile(r'"([^"\n]+)"')
# Characters Obsidian leaves unencoded in markdown link URLs (encodeURI-like).
_URL_SAFE_CHARS = "/()!$&'*+,;=@"
_NOTE_SUFFIX = ".md"


class Resolver:
    """Resolve link paths to vault files the way Obsidian does.

    Lookup is case-insensitive and tries, in order: relative to the linking
    note, from the vault root, then any file whose path ends with the link
    (closest to the linking note first, then the shortest path).
    """

    def __init__(self, files: list[str]) -> None:
        self._by_path = {file.lower(): file for file in files}
        self._by_name: dict[str, list[str]] = {}
        for file in files:
            self._by_name.setdefault(posixpath.basename(file).lower(), []).append(file)

    def resolve(self, link: str, source: str, *, exact: bool = False) -> str | None:
        """Return the vault file ``link`` points to from ``source``, or None.

        ``exact`` skips the file-name fallback - markdown links must point at
        the real path to keep working outside Obsidian.
        """
        link = link.strip()
        if not link:
            return None
        candidates = [link] if link.lower().endswith(_NOTE_SUFFIX) else [link, link + _NOTE_SUFFIX]
        source_dir = posixpath.dirname(source)
        for candidate in candidates:
            for base in (source_dir, ""):
                joined = posixpath.normpath(posixpath.join(base, candidate.lstrip("/")))
                if not joined.startswith("../") and joined.lower() in self._by_path:
                    return self._by_path[joined.lower()]
        if exact:
            return None
        for candidate in candidates:
            match = self._closest_suffix_match(candidate.lower(), source_dir)
            if match:
                return match
        return None

    def is_unique_name(self, name: str) -> bool:
        """True when exactly one file has this file name."""
        return len(self._by_name.get(name.lower(), [])) == 1

    def _closest_suffix_match(self, link: str, source_dir: str) -> str | None:
        """Pick the best file whose path ends with ``link``."""
        matches = [
            file
            for file in self._by_name.get(posixpath.basename(link), [])
            if file.lower() == link or file.lower().endswith("/" + link)
        ]
        if not matches:
            return None
        return min(
            matches,
            key=lambda file: (posixpath.dirname(file) != source_dir, file.count("/"), file),
        )


@dataclass
class LinkContext:
    """Everything needed to rewrite the links inside one note."""

    source_old: str
    source_new: str
    old: Resolver
    new: Resolver
    moved: dict[str, str]
    moved_folder: tuple[str, str] | None = None
    changes: list[dict] = field(default_factory=list)


def rewrite_links(text: str, context: LinkContext) -> str:
    """Rewrite every link in ``text`` that the move would break.

    Rewrites are recorded as ``{before, after}`` in ``context.changes``.
    """
    parts: list[str] = []
    position = 0
    for fence in _FENCE_RE.finditer(text):
        parts.append(_rewrite_prose(text[position:fence.start()], context))
        is_dataview = fence.group(2).strip().lower() == "dataview"
        parts.append(_rewrite_dataview(fence.group(0), context) if is_dataview else fence.group(0))
        position = fence.end()
    parts.append(_rewrite_prose(text[position:], context))
    return "".join(parts)


def _rewrite_prose(chunk: str, context: LinkContext) -> str:
    """Rewrite links in text outside fenced blocks, skipping inline code."""
    pieces = _INLINE_CODE_RE.split(chunk)
    for i in range(0, len(pieces), 2):
        piece = _WIKILINK_RE.sub(lambda m: _rewrite_wikilink(m, context), pieces[i])
        pieces[i] = _MARKDOWN_LINK_RE.sub(lambda m: _rewrite_markdown_link(m, context), piece)
    return "".join(pieces)


def _rewrite_wikilink(match: re.Match, context: LinkContext) -> str:
    """Rewrite one ``[[target#heading|alias]]``, keeping heading and alias."""
    target = match.group(1)
    # An alias pipe escaped for a table (``[[Note\|alias]]``) leaves a trailing backslash.
    escape = "\\" if target.endswith("\\") else ""
    link = target[: -len(escape)] if escape else target
    new_target = _moved_target(link, context)
    if new_target is None:
        return match.group(0)
    replaced = f"[[{_wikilink_path(link, new_target, context)}{escape}{match.group(2) or ''}{match.group(3) or ''}]]"
    return _record(match.group(0), replaced, context)


def _rewrite_markdown_link(match: re.Match, context: LinkContext) -> str:
    """Rewrite one ``](url)`` markdown link target as a relative path."""
    raw = match.group(2)
    bracketed = raw.startswith("<")
    url = raw[1:-1] if bracketed else raw
    if not url or url.startswith("#") or _URL_SCHEME_RE.match(url):
        return match.group(0)
    path, hash_sign, fragment = url.partition("#")
    link = path if bracketed else unquote(path)
    new_target = _moved_target(link, context, exact=True)
    if new_target is None:
        return match.group(0)
    relative = posixpath.relpath(new_target, posixpath.dirname(context.source_new) or ".")
    encoded = relative if bracketed else quote(relative, safe=_URL_SAFE_CHARS)
    new_url = f"{encoded}{hash_sign}{fragment}"
    replaced = f"{match.group(1)}{f'<{new_url}>' if bracketed else new_url}{match.group(3)}"
    return _record(match.group(0), replaced, context)


def _rewrite_dataview(block: str, context: LinkContext) -> str:
    """Rewrite quoted folder/note paths in a ```dataview block."""

    def replace(match: re.Match) -> str:
        new_value = _moved_vault_path(match.group(1), context)
        if new_value is None:
            return match.group(0)
        return _record(match.group(0), f'"{new_value}"', context)

    return _QUOTED_RE.sub(replace, block)


def _moved_target(link: str, context: LinkContext, *, exact: bool = False) -> str | None:
    """New path of the file ``link`` points to, or None if the link still works.

    Only links where the note or its target moved are considered - an
    unrelated link that already relies on Obsidian's file-name fallback is
    not "fixed" as a side effect. ``exact`` keeps an exact-path link exact.
    """
    old_target = context.old.resolve(link, context.source_old)
    if old_target is None:
        return None
    new_target = context.moved.get(old_target, old_target)
    if new_target == old_target and context.source_new == context.source_old:
        return None
    exact = exact and context.old.resolve(link, context.source_old, exact=True) == old_target
    if context.new.resolve(link, context.source_new, exact=exact) == new_target:
        return None
    return new_target


def _wikilink_path(link: str, new_target: str, context: LinkContext) -> str:
    """Wikilink text for ``new_target``: a bare name when unique, else a vault path."""
    keep_suffix = link.lower().endswith(_NOTE_SUFFIX) or not new_target.lower().endswith(_NOTE_SUFFIX)
    shown = new_target if keep_suffix else new_target[: -len(_NOTE_SUFFIX)]
    if "/" not in link.strip():
        name = posixpath.basename(shown)
        if context.new.is_unique_name(posixpath.basename(new_target)):
            return name
    return shown


def _moved_vault_path(value: str, context: LinkContext) -> str | None:
    """Map a vault path quoted in a Dataview query to its new location."""
    path = value.strip("/")
    if path + _NOTE_SUFFIX in context.moved:
        return context.moved[path + _NOTE_SUFFIX][: -len(_NOTE_SUFFIX)]
    if context.moved_folder is None:
        return None
    old_folder, new_folder = context.moved_folder
    if path == old_folder or path.startswith(old_folder + "/"):
        return new_folder + path[len(old_folder):]
    return None


def _record(before: str, after: str, context: LinkContext) -> str:
    """Remember one rewrite for reporting and return the new text."""
    context.changes.append({"before": before, "after": after})
    return after
