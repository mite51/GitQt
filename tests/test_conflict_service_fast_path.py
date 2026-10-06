from __future__ import annotations

from gitqt.parsers.status import WorkingTreeEntry, WorkingTreeState
from gitqt.services.conflict import ConflictService


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: list[str], **_kwargs) -> object:
        self.calls.append(tuple(args))
        return type("Result", (), {"stdout": "", "stderr": ""})()


def test_conflict_service_skips_diff_subprocess_on_clean_snapshot() -> None:
    runner = _RecordingRunner()
    service = ConflictService(runner)  # type: ignore[arg-type]

    entries = [
        WorkingTreeEntry(
            path="a.txt",
            state=WorkingTreeState.MODIFIED,
            staged=True,
            unstaged=False,
            conflicted=False,
        ),
        WorkingTreeEntry(
            path="b.txt",
            state=WorkingTreeState.UNTRACKED,
            staged=False,
            unstaged=True,
            conflicted=False,
        ),
    ]

    snapshot = service.detect_from_snapshot("/tmp/repo", entries)

    assert snapshot.has_conflicts is False
    assert snapshot.conflicted_files == []
    assert runner.calls == []


def test_conflict_service_reuses_conflicted_paths_from_snapshot() -> None:
    runner = _RecordingRunner()
    service = ConflictService(runner)  # type: ignore[arg-type]

    entries = [
        WorkingTreeEntry(
            path="a.txt",
            state=WorkingTreeState.MODIFIED,
            staged=True,
            unstaged=True,
            conflicted=True,
        ),
    ]

    snapshot = service.detect_from_snapshot("/tmp/repo", entries)

    assert snapshot.has_conflicts
    assert snapshot.conflicted_files == ["a.txt"]
    assert runner.calls == []
