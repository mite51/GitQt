from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitqt.services.git import GitLocator, GitNotFoundError, GitRunner
from gitqt.services.status import StatusService


def _run_git(repo_path: Path, *args: str, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        input=input_text,
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


def _create_status_service() -> StatusService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return StatusService(GitRunner(executable))


def test_stage_and_unstage_file_changes(tmp_path: Path) -> None:
    repo_path = _create_committed_repo(tmp_path)
    file_path = repo_path / "tracked.txt"
    file_path.write_text("line one\nline two\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Initial commit")
    file_path.write_text("line one\nline two changed\n", encoding="utf-8")

    service = _create_status_service()
    service.stage_file(repo_path, "tracked.txt")
    staged_entry = next(entry for entry in service.get_working_tree(repo_path).entries)
    assert staged_entry.path == "tracked.txt"
    assert staged_entry.staged
    assert not staged_entry.unstaged

    service.unstage_file(repo_path, "tracked.txt")
    unstaged_entry = next(entry for entry in service.get_working_tree(repo_path).entries)
    assert unstaged_entry.path == "tracked.txt"
    assert not unstaged_entry.staged
    assert unstaged_entry.unstaged


def test_stage_hunk_only_stages_selected_hunk(tmp_path: Path) -> None:
    repo_path = _create_committed_repo(tmp_path)
    file_path = repo_path / "split.txt"
    file_path.write_text(
        "\n".join([f"line {index}" for index in range(1, 13)]) + "\n",
        encoding="utf-8",
    )
    _run_git(repo_path, "add", "--", "split.txt")
    _run_git(repo_path, "commit", "-m", "Initial split file")
    file_path.write_text(
        "\n".join(
            [
                "line 1",
                "line 2 changed",
                "line 3",
                "line 4",
                "line 5",
                "line 6",
                "line 7",
                "line 8",
                "line 9",
                "line 10",
                "line 11 changed",
                "line 12",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service = _create_status_service()
    unstaged_hunks_before = service.get_diff(repo_path, "split.txt")
    assert len(unstaged_hunks_before) == 2

    service.stage_hunk(repo_path, "split.txt", unstaged_hunks_before[0])

    staged_hunks = service.get_diff(repo_path, "split.txt", staged=True)
    unstaged_hunks_after = service.get_diff(repo_path, "split.txt")
    status_entry = next(entry for entry in service.get_working_tree(repo_path).entries)

    assert len(staged_hunks) == 1
    assert len(unstaged_hunks_after) == 1
    assert status_entry.staged
    assert status_entry.unstaged


def test_status_service_caches_working_tree_until_ttl_expires() -> None:
    class _FakeRunner:
        def __init__(self) -> None:
            self.status_calls = 0

        def run(self, args: list[str], *, cwd: str | Path) -> object:  # noqa: ARG002
            if args == ["status", "--porcelain", "-z", "--untracked-files=all"]:
                self.status_calls += 1
                return type("Result", (), {"stdout": " M tracked.txt\n"})()
            raise AssertionError(f"Unexpected command: {args}")

    times = iter([10.0, 10.1, 10.2, 10.8, 10.9])
    service = StatusService(  # type: ignore[arg-type]
        _FakeRunner(),
        cache_ttl_seconds=0.5,
        monotonic_func=lambda: next(times),
    )

    first = service.get_working_tree("/tmp/repo")
    second = service.get_working_tree("/tmp/repo")
    third = service.get_working_tree("/tmp/repo")
    fourth = service.get_working_tree("/tmp/repo")

    assert len(first.entries) == 1
    assert len(second.entries) == 1
    assert len(third.entries) == 1
    assert len(fourth.entries) == 1
    assert service._runner.status_calls == 2  # noqa: SLF001


def test_status_service_invalidates_cached_state_after_index_changes() -> None:
    class _FakeRunner:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []
            self.status_calls = 0

        def run(
            self,
            args: list[str],
            *,
            cwd: str | Path,  # noqa: ARG002
            input_text: str | None = None,  # noqa: ARG002
            env_overrides=None,
        ) -> object:
            self.calls.append(args)
            if args == ["status", "--porcelain", "-z", "--untracked-files=all"]:
                self.status_calls += 1
                return type("Result", (), {"stdout": " M tracked.txt\n"})()
            if args == ["add", "--", "tracked.txt"]:
                return type("Result", (), {"stdout": ""})()
            raise AssertionError(f"Unexpected command: {args}")

    service = StatusService(  # type: ignore[arg-type]
        _FakeRunner(),
        cache_ttl_seconds=5.0,
        monotonic_func=lambda: 1.0,
    )
    service.get_working_tree("/tmp/repo")
    service.stage_file("/tmp/repo", "tracked.txt")
    service.get_working_tree("/tmp/repo")

    assert service._runner.status_calls == 2  # noqa: SLF001
