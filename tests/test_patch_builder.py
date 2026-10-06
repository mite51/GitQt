from __future__ import annotations

from gitqt.parsers.diff import DiffHunk, DiffLine, DiffLineType
from gitqt.parsers.patch import build_patch_for_hunk


def test_build_patch_for_hunk_renders_valid_unified_diff_snippet() -> None:
    hunk = DiffHunk(
        header="@@ -1,2 +1,3 @@",
        old_start=1,
        old_count=2,
        new_start=1,
        new_count=3,
        lines=[
            DiffLine(
                type=DiffLineType.CONTEXT,
                text="line one",
                old_line_number=1,
                new_line_number=1,
            ),
            DiffLine(
                type=DiffLineType.DELETION,
                text="line two",
                old_line_number=2,
                new_line_number=None,
            ),
            DiffLine(
                type=DiffLineType.ADDITION,
                text="line two changed",
                old_line_number=None,
                new_line_number=2,
            ),
            DiffLine(
                type=DiffLineType.ADDITION,
                text="line two point five",
                old_line_number=None,
                new_line_number=3,
            ),
        ],
    )

    patch = build_patch_for_hunk("src/file.txt", hunk)

    assert "diff --git a/src/file.txt b/src/file.txt" in patch
    assert "--- a/src/file.txt" in patch
    assert "+++ b/src/file.txt" in patch
    assert "@@ -1,2 +1,3 @@" in patch
    assert " line one" in patch
    assert "-line two" in patch
    assert "+line two changed" in patch
