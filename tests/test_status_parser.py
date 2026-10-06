from __future__ import annotations

from gitqt.parsers.status import WorkingTreeState, parse_porcelain_status


def test_parse_porcelain_status_covers_mixed_file_states() -> None:
    stdout = "\n".join(
        [
            " M src/modified.py",
            "A  src/added.py",
            " D src/deleted.py",
            "?? src/untracked.py",
            "R  src/old_name.py -> src/new_name.py",
        ]
    )

    entries = parse_porcelain_status(stdout)

    by_path = {entry.path: entry for entry in entries}
    assert by_path["src/modified.py"].state == WorkingTreeState.MODIFIED
    assert by_path["src/modified.py"].unstaged
    assert by_path["src/added.py"].state == WorkingTreeState.ADDED
    assert by_path["src/added.py"].staged
    assert by_path["src/deleted.py"].state == WorkingTreeState.DELETED
    assert by_path["src/deleted.py"].unstaged
    assert by_path["src/untracked.py"].state == WorkingTreeState.UNTRACKED
    assert not by_path["src/untracked.py"].staged
    assert by_path["src/new_name.py"].state == WorkingTreeState.MODIFIED


def test_parse_porcelain_status_strips_quotes_from_paths_with_spaces() -> None:
    stdout = "\n".join(
        [
            '?? "README (Copy).md"',
            ' M "file with spaces.txt"',
            'R  "old name.py" -> "new name.py"',
        ]
    )

    entries = parse_porcelain_status(stdout)

    by_path = {entry.path: entry for entry in entries}
    assert "README (Copy).md" in by_path
    assert by_path["README (Copy).md"].state == WorkingTreeState.UNTRACKED
    assert "file with spaces.txt" in by_path
    assert by_path["file with spaces.txt"].state == WorkingTreeState.MODIFIED
    assert "new name.py" in by_path
    assert by_path["new name.py"].state == WorkingTreeState.MODIFIED
