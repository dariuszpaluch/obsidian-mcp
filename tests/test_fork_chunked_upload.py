"""Fork: chunked attachment upload over MCP tool calls."""
from __future__ import annotations

import base64
import hashlib

import pytest

from obsidian_mcp.storage.policy import InvalidFileTypeError
from obsidian_mcp.tools import chunked_upload
from obsidian_mcp.tools.attachments import AttachmentTooLargeError
from obsidian_mcp.tools.chunked_upload import finish_attachment_upload, upload_attachment_chunk

_PDF = b"%PDF-1.7 " + bytes(range(256)) * 3


@pytest.fixture(autouse=True)
def _clear_uploads():
    chunked_upload._uploads.clear()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_chunks_are_joined_in_order(tmp_path, vault_factory):
    vault_factory({})
    first = upload_attachment_chunk("docs/report.pdf", _b64(_PDF[:300]), 0)
    upload_id = first["upload_id"]
    upload_attachment_chunk("docs/report.pdf", _b64(_PDF[500:]), 2, upload_id)
    upload_attachment_chunk("docs/report.pdf", _b64(_PDF[300:500]), 1, upload_id)
    result = finish_attachment_upload(upload_id, hashlib.sha256(_PDF).hexdigest())
    assert (tmp_path / "docs/report.pdf").read_bytes() == _PDF
    assert result["chunks"] == 3
    assert upload_id not in chunked_upload._uploads


def test_missing_chunk_is_reported(tmp_path, vault_factory):
    vault_factory({})
    upload_id = upload_attachment_chunk("a.pdf", _b64(b"a"), 0)["upload_id"]
    upload_attachment_chunk("a.pdf", _b64(b"c"), 2, upload_id)
    with pytest.raises(ValueError, match=r"Missing chunk\(s\) \[1\]"):
        finish_attachment_upload(upload_id)


def test_sha_mismatch_keeps_upload_for_resend(tmp_path, vault_factory):
    vault_factory({})
    upload_id = upload_attachment_chunk("a.pdf", _b64(b"broken"), 0)["upload_id"]
    with pytest.raises(ValueError, match="sha256 mismatch"):
        finish_attachment_upload(upload_id, hashlib.sha256(b"fixed").hexdigest())
    assert not (tmp_path / "a.pdf").exists()
    upload_attachment_chunk("a.pdf", _b64(b"fixed"), 0, upload_id)
    finish_attachment_upload(upload_id, hashlib.sha256(b"fixed").hexdigest())
    assert (tmp_path / "a.pdf").read_bytes() == b"fixed"


def test_size_limit_applies_to_the_whole_upload(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("MAX_ATTACHMENT_BYTES", "10")
    vault_factory({})
    upload_id = upload_attachment_chunk("a.pdf", _b64(b"123456"), 0)["upload_id"]
    with pytest.raises(AttachmentTooLargeError):
        upload_attachment_chunk("a.pdf", _b64(b"123456"), 1, upload_id)


def test_upload_id_is_bound_to_its_path(tmp_path, vault_factory):
    vault_factory({})
    upload_id = upload_attachment_chunk("a.pdf", _b64(b"x"), 0)["upload_id"]
    with pytest.raises(ValueError, match="belongs to"):
        upload_attachment_chunk("b.pdf", _b64(b"y"), 1, upload_id)


def test_unknown_upload_id_and_bad_extension_are_refused(tmp_path, vault_factory):
    vault_factory({})
    with pytest.raises(ValueError, match="Unknown or expired"):
        finish_attachment_upload("nope")
    with pytest.raises(InvalidFileTypeError):
        upload_attachment_chunk("script.sh", _b64(b"x"), 0)
