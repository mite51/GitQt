from __future__ import annotations

from gitqt.parsers.diff import DiffHunk, DiffLineType


def _quote_path(path: str) -> str:
    if all(32 < byte < 127 and byte not in (34, 92) for byte in path.encode("utf-8")):
        return path
    # Git's quoted path format escapes bytes, not Unicode code points.
    return (
        '"'
        + "".join(
            chr(byte) if 32 <= byte < 127 and byte not in (34, 92) else f"\\{byte:03o}"
            for byte in path.encode("utf-8")
        )
        + '"'
    )


def build_patch_for_hunk(
    file_path: str,
    hunk: DiffHunk,
    *,
    selected_lines: set[int] | None = None,
    reverse: bool = False,
    new_file: bool = False,
    mode: str = "100644",
) -> str:
    """Build a change against the target side, retaining unselected source lines.

    Reverse selection is built against the original *new* side: simply reversing
    a partially filtered forward patch would match the wrong source content.
    """
    body: list[str] = []
    old_count = new_count = 0
    included_previous = False
    changes = 0
    for index, line in enumerate(hunk.lines):
        kind = line.type
        if kind == DiffLineType.META:
            if included_previous:
                body.append(line.text)
            continue
        if reverse:
            kind = {
                DiffLineType.ADDITION: DiffLineType.DELETION,
                DiffLineType.DELETION: DiffLineType.ADDITION,
            }.get(kind, kind)
        selected = selected_lines is None or index in selected_lines
        if not selected and kind == DiffLineType.ADDITION:
            included_previous = False
            continue
        if not selected and kind == DiffLineType.DELETION:
            kind = DiffLineType.CONTEXT
        prefix = {
            DiffLineType.CONTEXT: " ",
            DiffLineType.ADDITION: "+",
            DiffLineType.DELETION: "-",
        }[kind]
        body.append(prefix + line.text)
        included_previous = True
        old_count += kind != DiffLineType.ADDITION
        new_count += kind != DiffLineType.DELETION
        changes += kind != DiffLineType.CONTEXT
    if not changes:
        raise ValueError("Select at least one added or deleted line.")
    old_start = hunk.new_start if reverse else hunk.old_start
    new_start = old_start
    if not old_count:
        new_start = old_start + 1
    elif not new_count:
        new_start = max(0, old_start - 1)
    before, after = _quote_path(f"a/{file_path}"), _quote_path(f"b/{file_path}")
    header = [f"diff --git {before} {after}"]
    if new_file:
        header.append(f"new file mode {mode}")
    header.extend(
        [
            f"--- {'/dev/null' if new_file else before}",
            f"+++ {after}",
            f"@@ -{old_start},{old_count} +{new_start},{new_count} @@",
        ]
    )
    return "\n".join([*header, *body, ""])
