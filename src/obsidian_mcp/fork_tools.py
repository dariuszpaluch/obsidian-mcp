"""Tools added by the dariuszpaluch fork, kept out of upstream files.

Each group is opt-in through its own environment variable, so with none set
the server behaves exactly like upstream:

- ``ENABLE_LINK_SAFE_MOVE`` - ``move_tool``: move/rename a note, attachment or
  folder and fix wikilinks, markdown links, embeds and Dataview paths.
- ``ENABLE_ARCHIVE`` - ``archive_tool``: move into ``ARCHIVE_FOLDER``
  (default ``_99_Archives``) instead of deleting.
- ``ENABLE_CHUNKED_UPLOAD`` - ``upload_attachment_chunk_tool`` and
  ``finish_attachment_upload_tool``: upload attachments too big for one call.
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

from .domain.index import VaultIndex
from .envelope import write_result
from .tools.audit import log_write
from .tools.chunked_upload import finish_attachment_upload, upload_attachment_chunk
from .tools.relocate import archive, relocate


def _enabled(name: str) -> bool:
    """Read a boolean feature flag from the environment."""
    return os.environ.get(name, "false").lower() in ("1", "true", "yes")


def _envelope(result: dict, path_key: str) -> dict:
    """Wrap a tool-layer result in the standard write envelope."""
    data = dict(result)
    path = data.pop(path_key)
    action = data.pop("status")
    return write_result(path, action, data=data, meta={"dry_run": True} if action == "dry_run" else {})


def register_fork_tools(mcp: FastMCP, index: VaultIndex) -> None:
    """Register the enabled fork tool groups on ``mcp``."""
    if _enabled("ENABLE_LINK_SAFE_MOVE"):
        _register_move(mcp, index)
    if _enabled("ENABLE_ARCHIVE"):
        _register_archive(mcp, index)
    if _enabled("ENABLE_CHUNKED_UPLOAD"):
        _register_chunked_upload(mcp)


def _register_move(mcp: FastMCP, index: VaultIndex) -> None:
    """Register ``move_tool``."""

    @mcp.tool()
    def move_tool(from_path: str, to_path: str, dry_run: bool = False, vault: str | None = None) -> dict:
        """Move or rename a note, attachment or folder - like dragging it in
        Obsidian. Every link that would break is fixed across the vault:
        [[wikilinks]] (incl. #heading and |alias), ![[embeds]],
        [text](relative/path.md) markdown links, and folder paths in ```dataview
        FROM "..." queries. Links inside code are left alone. A link that still
        resolves after the move is not touched.
        to_path is the full new path (e.g. 'Bike/Archive Trips/Trip.md' or
        'Projects/New Name' for a folder); the target must not exist.
        dry_run=True only reports what would change.
        data: {kind, files_moved, updated_links_in, link_changes,
        changes: [{path, before, after}] (first 100), changes_truncated}."""
        result = relocate(from_path, to_path, dry_run=dry_run, index=index)
        if not dry_run:
            log_write("move_tool", result["to"], f"moved from {result['from']} ({result['link_changes']} link(s) fixed)")
        return _envelope(result, "to")


def _register_archive(mcp: FastMCP, index: VaultIndex) -> None:
    """Register ``archive_tool``."""

    @mcp.tool()
    def archive_tool(path: str, dry_run: bool = False, vault: str | None = None) -> dict:
        """Archive a note, attachment or folder instead of deleting it: moves
        it to the archive folder (ARCHIVE_FOLDER, default '_99_Archives')
        under its original path, e.g. 'Bike/Trip.md' ->
        '_99_Archives/Bike/Trip.md'. Links to it are fixed like move_tool.
        Use this when the user asks to delete or remove something - the user
        deletes for good from Obsidian.
        data: same as move_tool."""
        result = archive(path, dry_run=dry_run, index=index)
        if not dry_run:
            log_write("archive_tool", result["to"], f"archived from {result['from']}")
        return _envelope(result, "to")


def _register_chunked_upload(mcp: FastMCP) -> None:
    """Register the chunked attachment upload tools."""

    @mcp.tool()
    def upload_attachment_chunk_tool(
        path: str,
        content_base64: str,
        chunk_index: int,
        upload_id: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Upload an attachment (PDF, image...) in pieces when it is too big
        for one add_attachment_tool call. Split the file's bytes into chunks
        of ~24 KB, base64-encode each one, and send them in order with
        chunk_index 0, 1, 2... The first call (no upload_id) returns an
        upload_id - pass it on every later chunk. Re-sending a chunk_index
        replaces it. Then call finish_attachment_upload_tool.
        Unfinished uploads expire after 1 hour.
        data: {upload_id, chunk_index, chunks_received, bytes_received}."""
        return _envelope(upload_attachment_chunk(path, content_base64, chunk_index, upload_id), "path")

    @mcp.tool()
    def finish_attachment_upload_tool(upload_id: str, sha256: str | None = None, vault: str | None = None) -> dict:
        """Finish a chunked upload: joins the chunks in order and writes the
        attachment. Pass the whole file's sha256 hex digest when you can - a
        mismatch means a chunk got corrupted, so the file is not written and
        the chunks can be re-sent.
        data: {size_bytes, mime_type, sha256, chunks}."""
        result = finish_attachment_upload(upload_id, sha256)
        log_write("finish_attachment_upload_tool", result["path"], f"uploaded {result['size_bytes']} bytes in {result['chunks']} chunk(s)")
        return _envelope(result, "path")
