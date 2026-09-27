"""Link-safe move and archive for notes, attachments and folders.

Fork addition: unlike upstream ``move_note``/``rename_folder`` (wikilinks
only), this rewrites wikilinks, markdown links, embeds and Dataview folder
paths - see :mod:`.links`.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from ..config import get_config
from ..domain.index import VaultIndex
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock
from .links import LinkContext, Resolver, rewrite_links

_DEFAULT_ARCHIVE_FOLDER = "_99_Archives"
# Keeps a large folder move from flooding the caller's context.
_MAX_REPORTED_CHANGES = 100


@dataclass
class _Rewrite:
    """One note whose links change: its current path, old text and new text."""

    path: str
    before: str
    after: str
    changes: list[dict]


def relocate(
    from_path: str,
    to_path: str,
    *,
    dry_run: bool = False,
    index: VaultIndex | None = None,
) -> dict:
    """Move a note, attachment or folder and fix every link that would break."""
    storage = VaultStorage.from_config()
    source = storage.resolve_delete(from_path).relative
    destination = storage.resolve_write(to_path).relative
    _check_move(storage, source, destination)
    is_folder = stat.S_ISDIR(storage.stat(source, read=False).st_mode)
    authorization = storage.authorize_tree(source, destination=destination)

    files = [item.relative for item in storage.list_files()]
    moved = _moved_files(authorization.paths, source, destination, set(files))
    rewrites = _plan_rewrites(storage, files, moved, (source, destination) if is_folder else None)
    for rewrite in rewrites:
        storage.resolve_write(rewrite.path)  # preflight before any mutation

    result = _summary(source, destination, is_folder, moved, rewrites, dry_run)
    if dry_run:
        return result
    _apply(storage, source, destination, authorization, rewrites)
    _update_index(storage, index, moved, rewrites)
    return result


def archive(path: str, *, dry_run: bool = False, index: VaultIndex | None = None) -> dict:
    """Move ``path`` into the archive folder, keeping its folder structure."""
    archive_folder = os.environ.get("ARCHIVE_FOLDER", _DEFAULT_ARCHIVE_FOLDER).strip("/")
    source = VaultStorage.from_config().resolve_delete(path).relative
    if source == archive_folder or source.startswith(archive_folder + "/"):
        raise ValueError(f"Already archived: {source!r}")
    return relocate(source, f"{archive_folder}/{source}", dry_run=dry_run, index=index)


def _check_move(storage: VaultStorage, source: str, destination: str) -> None:
    """Reject moves that cannot succeed before touching anything."""
    if not storage.exists(source, read=False):
        raise FileNotFoundError(f"Path not found: {source!r}")
    if storage.exists(destination, read=False):
        raise FileExistsError(f"Target already exists: {destination!r}")
    if destination.startswith(source + "/"):
        raise ValueError("Destination cannot be inside the source folder")


def _moved_files(
    tree: tuple[str, ...], source: str, destination: str, files: set[str]
) -> dict[str, str]:
    """Map each moved file's old path to its new path."""
    prefix = source + "/"
    return {
        path: destination + path[len(source):] if path.startswith(prefix) else destination
        for path in tree
        if path in files
    }


def _plan_rewrites(
    storage: VaultStorage,
    files: list[str],
    moved: dict[str, str],
    moved_folder: tuple[str, str] | None,
) -> list[_Rewrite]:
    """Find every note whose links the move would break, with its fixed text."""
    old = Resolver(files)
    new = Resolver([moved.get(file, file) for file in files])
    rewrites: list[_Rewrite] = []
    for file in files:
        if not file.lower().endswith(".md"):
            continue
        try:
            text = storage.read_text(file)
        except Exception:
            continue
        context = LinkContext(file, moved.get(file, file), old, new, moved, moved_folder)
        rewritten = rewrite_links(text, context)
        if rewritten != text:
            rewrites.append(_Rewrite(file, text, rewritten, context.changes))
    return rewrites


def _apply(
    storage: VaultStorage,
    source: str,
    destination: str,
    authorization,
    rewrites: list[_Rewrite],
) -> None:
    """Write link fixes, then move; restore the old texts if anything fails."""
    lock_path = get_config().lock_path
    locks = []
    written: list[_Rewrite] = []
    try:
        for path in dict.fromkeys([*(rewrite.path for rewrite in rewrites), source]):
            locks.append(acquire_lock(path, lock_path=lock_path))
        for rewrite in rewrites:
            if storage.read_text(rewrite.path) != rewrite.before:
                raise RuntimeError(f"{rewrite.path!r} changed during the move - nothing was moved, retry")
        try:
            for rewrite in rewrites:
                storage.write_text_atomic(rewrite.path, rewrite.after)
                written.append(rewrite)
            storage.move(source, destination, authorization=authorization)
        except Exception:
            for rewrite in written:
                storage.write_text_atomic(rewrite.path, rewrite.before)
            raise
    finally:
        for lock in reversed(locks):
            lock.release()


def _update_index(
    storage: VaultStorage,
    index: VaultIndex | None,
    moved: dict[str, str],
    rewrites: list[_Rewrite],
) -> None:
    """Refresh the search/backlink index for moved and rewritten notes."""
    if index is None:
        return
    for old_path, new_path in moved.items():
        if not old_path.lower().endswith(".md"):
            continue
        index.remove(old_path)
        if storage.policy.can_read(new_path):
            index.update(new_path)
    for rewrite in rewrites:
        if rewrite.path not in moved:
            index.update(rewrite.path)


def _summary(
    source: str,
    destination: str,
    is_folder: bool,
    moved: dict[str, str],
    rewrites: list[_Rewrite],
    dry_run: bool,
) -> dict:
    """Describe the move: what moved, which notes' links changed and how."""
    changes = [
        {"path": moved.get(rewrite.path, rewrite.path), **change}
        for rewrite in rewrites
        for change in rewrite.changes
    ]
    kind = "folder" if is_folder else "note" if source.lower().endswith(".md") else "file"
    return {
        "from": source,
        "to": destination,
        "status": "dry_run" if dry_run else "moved",
        "kind": kind,
        "files_moved": len(moved),
        "updated_links_in": [moved.get(rewrite.path, rewrite.path) for rewrite in rewrites],
        "link_changes": len(changes),
        "changes": changes[:_MAX_REPORTED_CHANGES],
        "changes_truncated": len(changes) > _MAX_REPORTED_CHANGES,
    }
