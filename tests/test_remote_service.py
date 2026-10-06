from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitqt.services.git import (
    GitCommandFailure,
    GitCommandResult,
    GitExecutable,
    GitLocator,
    GitNotFoundError,
    GitRunner,
)
from gitqt.services.remote import RemoteService


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


def _create_remote_service() -> RemoteService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return RemoteService(GitRunner(executable))


class _FakeRemoteRunner:
    def __init__(self) -> None:
        self.executable = GitExecutable(path="git")
        self.calls: list[list[str]] = []
        self._failures: dict[str, str] = {}

    def fail(self, operation: str, stderr: str) -> None:
        self._failures[operation] = stderr

    def clear_failures(self) -> None:
        self._failures = {}

    def run(self, args: list[str], **kwargs) -> GitCommandResult:  # noqa: ARG002
        self.calls.append(list(args))
        operation = args[0]
        if operation in self._failures:
            raise GitCommandFailure(
                GitCommandResult(
                    args=("git", *args),
                    stdout="",
                    stderr=self._failures[operation],
                    exit_code=1,
                    elapsed_ms=55,
                )
            )
        if args[:2] == ["remote", "-v"]:
            return GitCommandResult(
                args=("git", *args),
                stdout=(
                    "origin\tgit@github.com:org/repo.git (fetch)\n"
                    "origin\tgit@github.com:org/repo.git (push)\n"
                    "upstream\thttps://github.com/org/repo.git (fetch)\n"
                    "upstream\thttps://github.com/org/repo.git (push)\n"
                ),
                stderr="",
                exit_code=0,
                elapsed_ms=10,
            )
        return GitCommandResult(
            args=("git", *args),
            stdout="",
            stderr="receiving objects: 100%",
            exit_code=0,
            elapsed_ms=1200,
        )


def test_remote_service_orchestrates_remote_commands_and_options() -> None:
    runner = _FakeRemoteRunner()
    service = RemoteService(runner)  # type: ignore[arg-type]

    service.fetch("/tmp/repo", remote="upstream", prune=True)
    service.pull("/tmp/repo", remote="origin", branch="main", rebase=True)
    service.push("/tmp/repo", remote="origin", branch="main", set_upstream=True)

    assert runner.calls == [
        ["fetch", "--progress", "upstream", "--prune"],
        ["pull", "--progress", "origin", "main", "--rebase"],
        ["push", "--progress", "origin", "main", "--set-upstream"],
    ]


def test_remote_service_maps_common_fetch_pull_push_failures() -> None:
    runner = _FakeRemoteRunner()
    service = RemoteService(runner)  # type: ignore[arg-type]

    runner.fail("fetch", "fatal: Authentication failed for 'https://example.com/repo.git'")
    fetch_result = service.fetch("/tmp/repo")
    assert not fetch_result.success
    assert fetch_result.error_category == "authentication_failed"
    assert "authentication failed" in fetch_result.message.lower()

    runner.clear_failures()
    runner.fail("pull", "CONFLICT (content): Merge conflict in tracked.txt")
    pull_result = service.pull("/tmp/repo")
    assert not pull_result.success
    assert pull_result.error_category == "merge_conflict"
    assert "merge conflicts" in pull_result.message.lower()

    runner.clear_failures()
    runner.fail(
        "push",
        " ! [rejected] main -> main (non-fast-forward)\nerror: failed to push some refs",
    )
    push_result = service.push("/tmp/repo")
    assert not push_result.success
    assert push_result.error_category == "non_fast_forward"
    assert "non-fast-forward" in push_result.message.lower()


def test_remote_service_lists_remote_urls_from_repository(tmp_path: Path) -> None:
    service = _create_remote_service()
    remote_path = tmp_path / "origin.git"
    _run_git(tmp_path, "init", "--bare", str(remote_path))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "remote", "add", "origin", str(remote_path))
    _run_git(repo_path, "remote", "add", "upstream", "https://example.com/upstream.git")

    snapshot = service.list_remotes(repo_path)

    names = [remote.name for remote in snapshot.remotes]
    assert names == ["origin", "upstream"]
    origin = next(remote for remote in snapshot.remotes if remote.name == "origin")
    assert origin.fetch_url == str(remote_path)
    assert origin.push_url == str(remote_path)
