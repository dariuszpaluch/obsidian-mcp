"""Chunked attachment upload over plain MCP tool calls.

Fork addition for clients without network access to the /attachments HTTP
route (e.g. the claude.ai chat sandbox): send the file as several small
base64 chunks, then finish the upload to write it atomically.

Uploads in progress are kept in memory - a server restart drops them, and the
caller simply starts again.
"""
from __future__ import annotations

import base64
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field

from ..config import get_config
from .attachments import AttachmentTooLargeError, validate_attachment_path, write_attachment_bytes

_UPLOAD_TTL_SECONDS = 3600
_MAX_OPEN_UPLOADS = 20

_uploads: dict[str, _Upload] = {}
_uploads_lock = threading.Lock()


@dataclass
class _Upload:
    """One upload in progress: target path, vault and received chunks."""

    path: str
    vault: str
    started_at: float = field(default_factory=time.monotonic)
    chunks: dict[int, bytes] = field(default_factory=dict)

    @property
    def size(self) -> int:
        """Bytes received so far."""
        return sum(len(chunk) for chunk in self.chunks.values())


def upload_attachment_chunk(
    path: str, content_base64: str, chunk_index: int, upload_id: str | None = None
) -> dict:
    """Store one chunk; the first call (no ``upload_id``) starts an upload."""
    cfg = get_config()
    if chunk_index < 0:
        raise ValueError("chunk_index must be 0 or greater")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 content: {exc}") from exc

    with _uploads_lock:
        _drop_expired()
        upload = _start(path, cfg) if upload_id is None else _find(upload_id, cfg)
        upload_id = upload_id or _register(upload)
        if upload.path != validate_attachment_path(path, write=True):
            raise ValueError(f"upload_id belongs to {upload.path!r}, not {path!r}")
        previous = len(upload.chunks.get(chunk_index, b""))
        if upload.size - previous + len(data) > cfg.max_attachment_bytes:
            raise AttachmentTooLargeError(
                f"Attachment exceeds MAX_ATTACHMENT_BYTES ({cfg.max_attachment_bytes} bytes)"
            )
        upload.chunks[chunk_index] = data  # re-sending an index replaces it
        return {
            "path": upload.path,
            "status": "chunk_received",
            "upload_id": upload_id,
            "chunk_index": chunk_index,
            "chunks_received": len(upload.chunks),
            "bytes_received": upload.size,
        }


def finish_attachment_upload(upload_id: str, sha256: str | None = None) -> dict:
    """Join the chunks in order, verify them and write the attachment."""
    cfg = get_config()
    with _uploads_lock:
        _drop_expired()
        upload = _find(upload_id, cfg)
        missing = sorted(set(range(max(upload.chunks) + 1)) - set(upload.chunks))
        if missing:
            raise ValueError(f"Missing chunk(s) {missing} - send them and finish again")
        data = b"".join(upload.chunks[index] for index in sorted(upload.chunks))
        digest = hashlib.sha256(data).hexdigest()
        if sha256 and sha256.lower() != digest:
            raise ValueError(
                f"sha256 mismatch (got {digest}) - a chunk was corrupted; re-send the chunks and finish again"
            )
        result = write_attachment_bytes(upload.path, data)
        del _uploads[upload_id]
    return {**result, "sha256": digest, "chunks": len(upload.chunks)}


def _start(path: str, cfg) -> _Upload:
    """Validate the target and create a new upload."""
    if len(_uploads) >= _MAX_OPEN_UPLOADS:
        raise RuntimeError("Too many uploads in progress - finish or wait for old ones to expire")
    return _Upload(path=validate_attachment_path(path, write=True), vault=cfg.resolve_vault_name())


def _register(upload: _Upload) -> str:
    """Store a new upload under an unguessable id."""
    upload_id = uuid.uuid4().hex
    _uploads[upload_id] = upload
    return upload_id


def _find(upload_id: str, cfg) -> _Upload:
    """Look up an upload that belongs to the current vault."""
    upload = _uploads.get(upload_id)
    if upload is None or upload.vault != cfg.resolve_vault_name():
        raise ValueError(f"Unknown or expired upload_id {upload_id!r} - start again without upload_id")
    return upload


def _drop_expired() -> None:
    """Forget uploads that were never finished."""
    now = time.monotonic()
    for upload_id in [key for key, upload in _uploads.items() if now - upload.started_at > _UPLOAD_TTL_SECONDS]:
        del _uploads[upload_id]
