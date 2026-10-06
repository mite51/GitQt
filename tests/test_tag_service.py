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
from gitqt.services.tag import TagService


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


def _create_tag_service() -> TagService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return TagService(GitRunner(executable))


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


class _FakeTagRunner:
    def __init__(self) -> None:
        self.executable = GitExecutable(path="git")
        self.calls: list[list[str]] = []

    def run(self, args: list[str], **kwargs) -> GitCommandResult:  # noqa: ARG002
        self.calls.append(list(args))
        if args[:2] == ["tag", "--list"]:
            return GitCommandResult(
                args=("git", *args),
                stdout="v1.1.0\nv1.0.0\n",
                stderr="",
                exit_code=0,
                elapsed_ms=8,
            )
        return GitCommandResult(
            args=("git", *args),
            stdout="",
            stderr="",
            exit_code=0,
            elapsed_ms=15,
        )


def test_tag_service_creates_lightweight_and_annotated_tags() -> None:
    runner = _FakeTagRunner()
    service = TagService(runner)  # type: ignore[arg-type]

    service.create_tag("/tmp/repo", "v1.2.0")
    service.create_tag("/tmp/repo", "v1.2.1", target="HEAD~1", annotation="release notes")

    assert runner.calls == [
        ["tag", "v1.2.0"],
        ["tag", "-a", "v1.2.1", "-m", "release notes", "HEAD~1"],
    ]


def test_tag_service_lists_created_tags_from_repository(tmp_path: Path) -> None:
    service = _create_tag_service()
    repo_path = _seed_repo(tmp_path)

    service.create_tag(repo_path, "v0.1.0")
    service.create_tag(repo_path, "v0.2.0-beta", annotation="beta build")
    snapshot = service.list_tags(repo_path)

    names = {tag.name for tag in snapshot.tags}
    assert {"v0.1.0", "v0.2.0-beta"}.issubset(names)
