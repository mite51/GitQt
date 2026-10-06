from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitqt.services.commit import CommitService
from gitqt.services.git import (
    GitCommandFailure,
    GitCommandResult,
    GitExecutable,
    GitLocator,
    GitNotFoundError,
    GitRunner,
)


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


def _create_committed_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "Test User")
    _run_git(repo_path, "config", "user.email", "test@example.com")
    return repo_path


def _create_commit_service() -> CommitService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return CommitService(GitRunner(executable))


def test_validate_message_rules() -> None:
    service = _create_commit_service()

    invalid_empty = service.validate_message("   ", "")
    assert not invalid_empty.is_valid
    assert "required" in invalid_empty.errors[0].lower()

    invalid_multiline = service.validate_message("summary\nnext", "")
    assert not invalid_multiline.is_valid
    assert any("single line" in message.lower() for message in invalid_multiline.errors)

    invalid_long = service.validate_message("x" * 73, "")
    assert not invalid_long.is_valid
    assert any("72" in message for message in invalid_long.errors)

    valid = service.validate_message("Add commit validation", "Body detail.")
    assert valid.is_valid
    assert valid.summary == "Add commit validation"
    assert valid.body == "Body detail."


def test_commit_execution_success(tmp_path: Path) -> None:
    repo_path = _create_committed_repo(tmp_path)
    file_path = repo_path / "tracked.txt"
    file_path.write_text("line one\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Initial commit")
    file_path.write_text("line one changed\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")

    service = _create_commit_service()
    result = service.commit(
        repo_path,
        summary="Update tracked content",
        body="Adjust the file to verify commit execution.",
    )

    assert result.success
    assert result.commit_hash
    latest_summary = _run_git(repo_path, "log", "-1", "--pretty=%s").strip()
    assert latest_summary == "Update tracked content"


def test_commit_rejects_empty_staged_state_without_allow_empty(tmp_path: Path) -> None:
    repo_path = _create_committed_repo(tmp_path)
    file_path = repo_path / "tracked.txt"
    file_path.write_text("line one\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Initial commit")

    service = _create_commit_service()
    result = service.commit(repo_path, summary="No changes")

    assert not result.success
    assert "no staged changes" in result.message.lower()


class _FailingCommitRunner:
    def __init__(self) -> None:
        self.executable = GitExecutable(path="git")

    def run(self, args: list[str], **kwargs) -> GitCommandResult:  # noqa: ARG002
        if args[:3] == ["diff", "--cached", "--name-only"]:
            return GitCommandResult(
                args=("git", *args),
                stdout="tracked.txt\n",
                stderr="",
                exit_code=0,
                elapsed_ms=1,
            )
        if args and args[0] == "commit":
            raise GitCommandFailure(
                GitCommandResult(
                    args=("git", *args),
                    stdout="",
                    stderr="simulated commit failure",
                    exit_code=1,
                    elapsed_ms=1,
                )
            )
        return GitCommandResult(
            args=("git", *args),
            stdout="abc123\n",
            stderr="",
            exit_code=0,
            elapsed_ms=1,
        )


def test_commit_execution_failure_surfaces_error() -> None:
    service = CommitService(_FailingCommitRunner())  # type: ignore[arg-type]

    result = service.commit("/tmp/repo", summary="Will fail")

    assert not result.success
    assert "simulated commit failure" in result.message
