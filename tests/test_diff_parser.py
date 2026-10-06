from __future__ import annotations

from gitqt.parsers.diff import DiffLineType, parse_unified_diff


def test_parse_unified_diff_tracks_hunks_and_line_numbers() -> None:
    stdout = "\n".join(
        [
            "diff --git a/file.txt b/file.txt",
            "index 123..456 100644",
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ -1,3 +1,4 @@",
            " line one",
            "-line two",
            "+line two changed",
            "+line two point five",
            " line three",
        ]
    )

    hunks = parse_unified_diff(stdout)

    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.old_start == 1
    assert hunk.old_count == 3
    assert hunk.new_start == 1
    assert hunk.new_count == 4

    assert hunk.lines[0].type == DiffLineType.CONTEXT
    assert hunk.lines[0].old_line_number == 1
    assert hunk.lines[0].new_line_number == 1
    assert hunk.lines[1].type == DiffLineType.DELETION
    assert hunk.lines[1].old_line_number == 2
    assert hunk.lines[1].new_line_number is None
    assert hunk.lines[2].type == DiffLineType.ADDITION
    assert hunk.lines[2].old_line_number is None
    assert hunk.lines[2].new_line_number == 2
