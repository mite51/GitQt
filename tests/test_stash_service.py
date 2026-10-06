from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitqt.services.git import (
    GitCommandResult,
    GitExecutable,
    GitLocator,
    GitNotFoundError,
    GitRunner,
)
from gitqt.services.stash import StashService


def _run_git(repo_path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _create_stash_service() -> StashService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return StashService(GitRunner(executable))


def _seed_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "Test User")
    _run_git(repo_path, "config", "user.email", "test@example.com")
    tracked = repo_path / "tracked.txt"
    tracked.write_text("line one\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Initial commit")
    return repo_path


class _FakeStashRunner:
    def __init__(self) -> None:
        self.executable = GitExecutable(path="git")
        self.calls: list[list[str]] = []

    def run(self, args: list[str], **kwargs) -> GitCommandResult:  # noqa: ARG002
        self.calls.append(list(args))
        if args[:2] == ["stash", "list"]:
            return GitCommandResult(
                args=("git", *args),
                stdout="stash@{0}|On main: WIP changes\n",
                stderr="",
                exit_code=0,
                elapsed_ms=10,
            )
        return GitCommandResult(
            args=("git", *args),
            stdout="",
            stderr="",
            exit_code=0,
            elapsed_ms=20,
        )


def test_stash_service_runs_create_apply_pop_drop_commands() -> None:
    runner = _FakeStashRunner()
    service = StashService(runner)  # type: ignore[arg-type]

    service.create_stash("/tmp/repo", message="WIP", include_untracked=True)
    service.apply_stash("/tmp/repo", "stash@{0}")
    service.pop_stash("/tmp/repo", "stash@{1}")
    service.drop_stash("/tmp/repo", "stash@{2}")

    assert runner.calls == [
        ["stash", "push", "--include-untracked", "-m", "WIP"],
        ["stash", "apply", "stash@{0}"],
        ["stash", "pop", "stash@{1}"],
        ["stash", "drop", "stash@{2}"],
    ]


def test_stash_service_creates_lists_and_drops_stashes(tmp_path: Path) -> None:
    service = _create_stash_service()
    repo_path = _seed_repo(tmp_path)
    tracked = repo_path / "tracked.txt"

    tracked.write_text("line one\nline two\n", encoding="utf-8")
    service.create_stash(repo_path, message="session-7", include_untracked=False)
    snapshot_after_create = service.list_stashes(repo_path)
    assert snapshot_after_create.entries
    assert snapshot_after_create.entries[0].ref == "stash@{0}"
    assert "session-7" in snapshot_after_create.entries[0].description

    service.drop_stash(repo_path, "stash@{0}")
    snapshot_after_drop = service.list_stashes(repo_path)
    assert snapshot_after_drop.entries == []
