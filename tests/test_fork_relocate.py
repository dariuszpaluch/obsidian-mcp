"""Fork: link-safe move_tool/archive_tool - wikilinks, markdown links,
embeds, Dataview paths, code skipping, rollback."""
from __future__ import annotations

import pytest

from obsidian_mcp.tools import relocate as relocate_mod
from obsidian_mcp.tools.relocate import archive, relocate


def _read(tmp_path, rel: str) -> str:
    return (tmp_path / rel).read_text(encoding="utf-8")


# ── wikilinks ─────────────────────────────────────────────────────────────

def test_rename_rewrites_bare_wikilink_keeping_heading_and_alias(tmp_path, vault_factory):
    vault_factory({"old.md": "x", "a.md": "[[old]] [[old#Intro]] [[old|Label]] ![[old]]"})
    relocate("old.md", "new.md")
    assert _read(tmp_path, "a.md") == "[[new]] [[new#Intro]] [[new|Label]] ![[new]]"


def test_move_rewrites_wikilink_with_folder_path(tmp_path, vault_factory):
    vault_factory({"Res/Obsidian/Note.md": "x", "a.md": "[[Res/Obsidian/Note|N]] [[Res/Obsidian/Note.md]]"})
    relocate("Res/Obsidian/Note.md", "Projects/Brain/Note.md")
    assert _read(tmp_path, "a.md") == "[[Projects/Brain/Note|N]] [[Projects/Brain/Note.md]]"


def test_plain_move_leaves_unique_bare_wikilink_and_file_untouched(tmp_path, vault_factory):
    vault_factory({"Bike/Trip.md": "x", "a.md": "[[Trip]]"})
    result = relocate("Bike/Trip.md", "Bike/Archive Trips/Trip.md")
    assert _read(tmp_path, "a.md") == "[[Trip]]"
    assert result["updated_links_in"] == []


def test_rename_uses_full_path_when_new_name_is_ambiguous(tmp_path, vault_factory):
    vault_factory({"A/old.md": "x", "B/new.md": "y", "c.md": "[[old]]"})
    relocate("A/old.md", "A/new.md")
    assert _read(tmp_path, "c.md") == "[[A/new]]"


def test_escaped_table_alias_pipe_is_kept(tmp_path, vault_factory):
    vault_factory({"old.md": "x", "a.md": "| [[old\\|Label]] |"})
    relocate("old.md", "new.md")
    assert _read(tmp_path, "a.md") == "| [[new\\|Label]] |"


# ── markdown links ────────────────────────────────────────────────────────

def test_move_rewrites_incoming_relative_markdown_link(tmp_path, vault_factory):
    vault_factory({"Bike/Trip Kraków.md": "x", "Bike/_Bike.md": "[t](Trip%20Krak%C3%B3w.md#Day%201)"})
    relocate("Bike/Trip Kraków.md", "Bike/Archive Trips/Trip Kraków.md")
    assert _read(tmp_path, "Bike/_Bike.md") == "[t](Archive%20Trips/Trip%20Krak%C3%B3w.md#Day%201)"


def test_moved_note_outgoing_relative_links_are_fixed(tmp_path, vault_factory):
    vault_factory({
        "Bike/_attachments/pic.png": "png",
        "Bike/Other.md": "y",
        "Bike/Trip.md": "![p](_attachments/pic.png) [o](Other.md) [[Other]]",
    })
    relocate("Bike/Trip.md", "Archive/Trip.md")
    assert _read(tmp_path, "Archive/Trip.md") == "![p](../Bike/_attachments/pic.png) [o](../Bike/Other.md) [[Other]]"


def test_unrelated_name_fallback_links_are_left_alone(tmp_path, vault_factory):
    # _attachments/pic.png only resolves through Obsidian's file-name fallback
    text = "![p](_attachments/pic.png)"
    vault_factory({"_attachments/pic.png": "png", "Sub/a.md": text, "old.md": "x"})
    result = relocate("old.md", "new.md")
    assert _read(tmp_path, "Sub/a.md") == text
    assert result["link_changes"] == 0


def test_moved_note_keeps_name_fallback_link_that_still_resolves(tmp_path, vault_factory):
    text = "![p](_attachments/pic.png)"
    vault_factory({"_attachments/pic.png": "png", "Sub/a.md": text})
    relocate("Sub/a.md", "Other/a.md")
    assert _read(tmp_path, "Other/a.md") == text


def test_angle_bracket_markdown_link_stays_unencoded(tmp_path, vault_factory):
    vault_factory({"My Note.md": "x", "a.md": "[t](<My Note.md>)"})
    relocate("My Note.md", "Sub/My Note.md")
    assert _read(tmp_path, "a.md") == "[t](<Sub/My Note.md>)"


def test_external_and_anchor_links_are_ignored(tmp_path, vault_factory):
    text = "[w](https://old.md) [h](#old) [m](mailto:a@b.c)"
    vault_factory({"old.md": "x", "a.md": text})
    relocate("old.md", "new.md")
    assert _read(tmp_path, "a.md") == text


# ── folders, dataview, code ───────────────────────────────────────────────

def test_folder_move_fixes_links_and_dataview_but_not_internal_links(tmp_path, vault_factory):
    vault_factory({
        "Res/Obsidian/_Obsidian.md": "[[Res/Obsidian/Tips]] [t](Tips.md)",
        "Res/Obsidian/Tips.md": "x",
        "hub.md": "[o](Res/Obsidian/_Obsidian.md)\n```dataview\nLIST FROM \"Res/Obsidian\"\n```\n",
    })
    result = relocate("Res/Obsidian", "Projects/Second Brain")
    assert result["kind"] == "folder"
    assert result["files_moved"] == 2
    assert _read(tmp_path, "Projects/Second Brain/_Obsidian.md") == "[[Projects/Second Brain/Tips]] [t](Tips.md)"
    assert _read(tmp_path, "hub.md") == (
        "[o](Projects/Second%20Brain/_Obsidian.md)\n```dataview\nLIST FROM \"Projects/Second Brain\"\n```\n"
    )


def test_links_inside_code_are_not_rewritten(tmp_path, vault_factory):
    text = "`[[old]]`\n```\n[[old]] [x](old.md)\n```\n"
    vault_factory({"old.md": "x", "a.md": text})
    relocate("old.md", "new.md")
    assert _read(tmp_path, "a.md") == text


# ── safety ────────────────────────────────────────────────────────────────

def test_dry_run_reports_changes_without_touching_files(tmp_path, vault_factory):
    vault_factory({"old.md": "x", "a.md": "[[old]]"})
    result = relocate("old.md", "new.md", dry_run=True)
    assert result["status"] == "dry_run"
    assert result["changes"] == [{"path": "a.md", "before": "[[old]]", "after": "[[new]]"}]
    assert (tmp_path / "old.md").exists()
    assert _read(tmp_path, "a.md") == "[[old]]"


def test_existing_target_is_refused(tmp_path, vault_factory):
    vault_factory({"old.md": "x", "new.md": "y"})
    with pytest.raises(FileExistsError):
        relocate("old.md", "new.md")


def test_failed_move_restores_rewritten_links(tmp_path, vault_factory, monkeypatch):
    vault_factory({"old.md": "x", "a.md": "[[old]]"})

    def failing_move(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(relocate_mod.VaultStorage, "move", failing_move)
    with pytest.raises(OSError):
        relocate("old.md", "new.md")
    assert _read(tmp_path, "a.md") == "[[old]]"
    assert (tmp_path / "old.md").exists()


def test_index_follows_the_move(tmp_path, vault_factory):
    index = vault_factory({"old.md": "x", "a.md": "[[old]]"})
    relocate("old.md", "new.md", index=index)
    assert "new.md" in index.get_all_notes()
    assert "old.md" not in index.get_all_notes()


# ── archive ───────────────────────────────────────────────────────────────

def test_archive_keeps_original_path_and_fixes_links(tmp_path, vault_factory):
    vault_factory({"Bike/Trip.md": "x", "a.md": "[t](Bike/Trip.md)"})
    result = archive("Bike/Trip.md")
    assert result["to"] == "_99_Archives/Bike/Trip.md"
    assert _read(tmp_path, "a.md") == "[t](_99_Archives/Bike/Trip.md)"


def test_archive_folder_is_configurable_and_refuses_archived_paths(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("ARCHIVE_FOLDER", "Old")
    vault_factory({"note.md": "x", "Old/done.md": "y"})
    assert archive("note.md")["to"] == "Old/note.md"
    with pytest.raises(ValueError, match="Already archived"):
        archive("Old/done.md")
