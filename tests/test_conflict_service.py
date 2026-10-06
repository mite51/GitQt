from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitqt.services.conflict import ConflictService
from gitqt.services.git import GitLocator, GitNotFoundError, GitRunner


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_git_ok(repo_path: Path, *args: str) -> str:
    completed = _run_git(repo_path, *args)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _create_repo(tmp_path: Path) -> tuple[Path, str]:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git_ok(repo_path, "init")
    _run_git_ok(repo_path, "config", "user.name", "Test User")
    _run_git_ok(repo_path, "config", "user.email", "test@example.com")
    default_branch = _run_git_ok(repo_path, "branch", "--show-current").strip()
    return repo_path, default_branch


def _create_conflict_service() -> ConflictService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return ConflictService(GitRunner(executable))


def test_conflict_service_detects_merge_conflicts(tmp_path: Path) -> None:
    repo_path, default_branch = _create_repo(tmp_path)
    tracked_file = repo_path / "tracked.txt"
    tracked_file.write_text("base\n", encoding="utf-8")
    _run_git_ok(repo_path, "add", "--", "tracked.txt")
    _run_git_ok(repo_path, "commit", "-m", "initial")

    _run_git_ok(repo_path, "checkout", "-b", "feature/conflict")
    tracked_file.write_text("feature change\n", encoding="utf-8")
    _run_git_ok(repo_path, "commit", "-am", "feature change")

    _run_git_ok(repo_path, "checkout", default_branch)
    tracked_file.write_text("mainline change\n", encoding="utf-8")
    _run_git_ok(repo_path, "commit", "-am", "mainline change")

    merge_attempt = _run_git(repo_path, "merge", "feature/conflict")
    assert merge_attempt.returncode != 0

    service = _create_conflict_service()
    snapshot = service.detect_conflicts(repo_path)

    assert snapshot.has_conflicts
    assert snapshot.operation == "merge"
    assert snapshot.conflicted_files == ["tracked.txt"]
    assert snapshot.banner_message is not None
    assert "merge conflicts detected" in snapshot.banner_message.lower()
    assert snapshot.recovery_message is not None
    assert "merge --abort" in snapshot.recovery_message


def test_conflict_service_detects_rebase_conflicts(tmp_path: Path) -> None:
    repo_path, default_branch = _create_repo(tmp_path)
    tracked_file = repo_path / "tracked.txt"
    tracked_file.write_text("base\n", encoding="utf-8")
    _run_git_ok(repo_path, "add", "--", "tracked.txt")
    _run_git_ok(repo_path, "commit", "-m", "initial")

    _run_git_ok(repo_path, "checkout", "-b", "feature/rebase-conflict")
    tracked_file.write_text("feature branch change\n", encoding="utf-8")
    _run_git_ok(repo_path, "commit", "-am", "feature change")

    _run_git_ok(repo_path, "checkout", default_branch)
    tracked_file.write_text("default branch change\n", encoding="utf-8")
    _run_git_ok(repo_path, "commit", "-am", "default branch change")

    _run_git_ok(repo_path, "checkout", "feature/rebase-conflict")
    rebase_attempt = _run_git(repo_path, "rebase", default_branch)
    assert rebase_attempt.returncode != 0

    service = _create_conflict_service()
    snapshot = service.detect_conflicts(repo_path)

    assert snapshot.has_conflicts
    assert snapshot.operation == "rebase"
    assert snapshot.conflicted_files == ["tracked.txt"]
    assert snapshot.banner_message is not None
    assert "rebase conflicts detected" in snapshot.banner_message.lower()
    assert snapshot.recovery_message is not None
    assert "rebase --continue" in snapshot.recovery_message
