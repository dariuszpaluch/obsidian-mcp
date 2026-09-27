# Fork Tools

Tools added in the `dariuszpaluch/obsidian-mcp` fork. They live in separate
files (`fork_tools.py`, `tools/links.py`, `tools/relocate.py`,
`tools/chunked_upload.py`, `tests/test_fork_*.py`) and hook into upstream code
with a single `register_fork_tools(mcp, _index)` call in `server.py`, so the
weekly upstream sync stays conflict-free.

Every group is off by default - with no flag set the server behaves exactly
like upstream.

| Flag | Tools | What it does |
|---|---|---|
| `ENABLE_LINK_SAFE_MOVE` | `move_tool` | Move or rename a note, attachment or folder and fix every link that would break |
| `ENABLE_ARCHIVE` | `archive_tool` | Move into `ARCHIVE_FOLDER` (default `_99_Archives`) under the original path - the AI's "delete" |
| `ENABLE_CHUNKED_UPLOAD` | `upload_attachment_chunk_tool`, `finish_attachment_upload_tool` | Upload an attachment in several base64 chunks |

## Link-safe move

Unlike upstream `move_note_tool` / `rename_folder_tool` (bare wikilinks only),
`move_tool` rewrites:

- `[[wikilinks]]` and `![[embeds]]`, with or without a folder path, keeping
  `#heading`, `|alias` and table-escaped `\|`
- `[text](relative/path.md)` and `![alt](_attachments/img.png)` markdown
  links - both links pointing at the moved file and the moved note's own
  relative links, written relative and URL-encoded like Obsidian does
- folder and note paths quoted in ```` ```dataview ```` blocks (`FROM "folder"`)

Rules:

- A link is changed only when the note or its target moved **and** the link
  would stop resolving to the same file. A plain move keeps a unique
  `[[Name]]` as it is and does not touch unrelated notes.
- A renamed bare `[[Name]]` becomes a vault path when the new name is not unique.
- Links inside inline code and fenced code blocks are left alone.
- Changed notes are checked under lock before writing; if a write or the move
  fails, the rewritten notes are restored.
- `dry_run=true` returns the planned `changes` without touching anything.

Do not enable upstream `ENABLE_MOVE` / `ENABLE_FOLDER_RENAME` next to it - they
only fix wikilinks and would break markdown links.

## Archive instead of delete

`archive_tool` moves `Bike/Trip.md` to `_99_Archives/Bike/Trip.md` with the
same link fixing as `move_tool`. Leave upstream `ENABLE_DELETE` off so the AI
cannot delete - delete for good from Obsidian.

## Chunked upload

For clients that cannot reach the `/attachments` HTTP route (e.g. the claude.ai
chat sandbox has no network access):

1. `upload_attachment_chunk_tool(path, content_base64, chunk_index=0)` - returns `upload_id`
2. repeat with `chunk_index=1, 2, ...` and the `upload_id`
3. `finish_attachment_upload_tool(upload_id, sha256=...)` - joins, verifies and writes

The usual attachment rules apply (allowed extensions, `MAX_ATTACHMENT_BYTES`
for the whole file). Uploads in progress are kept in memory and expire after
1 hour.
