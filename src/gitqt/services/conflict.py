from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from gitqt.parsers.status import WorkingTreeEntry
from gitqt.services.git import GitRunner


@dataclass(slots=True)
class ConflictSnapshot:
    has_conflicts: bool
    operation: str | None
    conflicted_files: list[str]
    banner_message: str | None
    recovery_message: str | None


_CLEAN_SNAPSHOT = ConflictSnapshot(
    has_conflicts=False,
    operation=None,
    conflicted_files=[],
    banner_message=None,
    recovery_message=None,
)


class ConflictService:
    """Detect repository conflict state for merge and rebase workflows."""

    def __init__(self, runner: GitRunner) -> None:
        self._runner = runner

    def detect_conflicts(self, repository_path: str | Path) -> ConflictSnapshot:
        return self._snapshot(repository_path, self._list_conflicted_files(repository_path))

    def detect_from_snapshot(
        self, repository_path: str | Path, entries: Iterable[WorkingTreeEntry]
    ) -> ConflictSnapshot:
        # Unmerged paths alone cannot tell us whether a sequencer is still running.
        return self._snapshot(repository_path, [e.path for e in entries if e.conflicted])

    def _snapshot(self, repository_path: str | Path, files: list[str]) -> ConflictSnapshot:
        operation = self._detect_operation(repository_path)
        if not files and operation is None:
            return _CLEAN_SNAPSHOT
        label = operation.capitalize() if operation else "Repository"
        banner = (
            f"{label} conflicts detected in {len(files)} file(s). Resolve and stage each file."
            if files
            else f"{label} in progress. Continue to finish, or abort the operation."
        )
        recovery = (
            f"Use Continue or Abort below (git {operation} --continue / git {operation} --abort)."
            if operation
            else "Resolve each conflicted file and stage the resolution."
        )
        return ConflictSnapshot(bool(files), operation, files, banner, recovery)

    def _list_conflicted_files(self, repository_path: str | Path) -> list[str]:
        result = self._runner.run(
            ["diff", "--name-only", "--diff-filter=U", "-z"], cwd=repository_path
        )
        return [path for path in result.stdout.split("\0") if path]

    def _detect_operation(self, repository_path: str | Path) -> str | None:
        git_dir = Path(repository_path) / ".git"
        if git_dir.is_file():
            # Linked worktrees and submodules keep operation state in their own gitdir.
            content = git_dir.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir: "):
                return None
            target = Path(content[8:])
            git_dir = target if target.is_absolute() else git_dir.parent / target
        if not git_dir.is_dir():
            return None
        if any((git_dir / name).is_dir() for name in ("rebase-merge", "rebase-apply")):
            return "rebase"
        for name, operation in (
            ("MERGE_HEAD", "merge"),
            ("CHERRY_PICK_HEAD", "cherry-pick"),
            ("REVERT_HEAD", "revert"),
        ):
            if (git_dir / name).is_file():
                return operation
        todo = git_dir / "sequencer" / "todo"
        if todo.is_file():
            for line in todo.read_text(encoding="utf-8").splitlines():
                if line.startswith("pick "):
                    return "cherry-pick"
                if line.startswith("revert "):
                    return "revert"
        return None
