from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitqt.services.branch import BranchService
from gitqt.services.git import GitLocator, GitNotFoundError, GitRunner


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


def _create_branch_service() -> BranchService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return BranchService(GitRunner(executable))


def _seed_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    remote_path = tmp_path / "remote.git"
    _run_git(tmp_path, "init", "--bare", str(remote_path))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "Test User")
    _run_git(repo_path, "config", "user.email", "test@example.com")

    tracked = repo_path / "tracked.txt"
    tracked.write_text("line one\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Initial commit")
    _run_git(repo_path, "branch", "-M", "main")
    _run_git(repo_path, "remote", "add", "origin", str(remote_path))
    _run_git(repo_path, "push", "-u", "origin", "main")
    _run_git(remote_path, "symbolic-ref", "HEAD", "refs/heads/main")
    return repo_path, remote_path


def test_branch_service_create_checkout_delete_and_detached_head(tmp_path: Path) -> None:
    repo_path, _ = _seed_repo_with_remote(tmp_path)
    tracked = repo_path / "tracked.txt"
    tracked.write_text("line one\nline two\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Second commit")

    service = _create_branch_service()
    service.create_branch(repo_path, "feature/session-5")
    service.checkout_branch(repo_path, "feature/session-5")

    snapshot_after_checkout = service.get_snapshot(repo_path)
    assert snapshot_after_checkout.tracking.branch_name == "feature/session-5"
    assert any(
        branch.name == "feature/session-5" for branch in snapshot_after_checkout.local_branches
    )

    service.checkout_branch(repo_path, "main")
    service.delete_branch(repo_path, "feature/session-5")
    snapshot_after_delete = service.get_snapshot(repo_path)
    assert all(
        branch.name != "feature/session-5" for branch in snapshot_after_delete.local_branches
    )

    service.checkout_branch(repo_path, "HEAD~1")
    detached_snapshot = service.get_snapshot(repo_path)
    assert detached_snapshot.tracking.is_detached_head
    assert detached_snapshot.tracking.branch_name is None
    assert detached_snapshot.tracking.head_commit_short_hash is not None


def test_branch_service_reports_ahead_behind_for_tracking_branch(tmp_path: Path) -> None:
    repo_path, remote_path = _seed_repo_with_remote(tmp_path)
    tracked = repo_path / "tracked.txt"
    tracked.write_text("line one\nline two\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Local ahead commit")

    collaborator_path = tmp_path / "collaborator"
    _run_git(tmp_path, "clone", str(remote_path), str(collaborator_path))
    _run_git(collaborator_path, "checkout", "main")
    _run_git(collaborator_path, "config", "user.name", "Collaborator")
    _run_git(collaborator_path, "config", "user.email", "collab@example.com")
    collaborator_file = collaborator_path / "tracked.txt"
    collaborator_file.write_text("line one\nline from collaborator\n", encoding="utf-8")
    _run_git(collaborator_path, "add", "--", "tracked.txt")
    _run_git(collaborator_path, "commit", "-m", "Remote ahead commit")
    _run_git(collaborator_path, "push", "origin", "main")

    _run_git(repo_path, "fetch", "origin")

    service = _create_branch_service()
    snapshot = service.get_snapshot(repo_path)

    assert snapshot.tracking.branch_name == "main"
    assert snapshot.tracking.upstream == "origin/main"
    assert snapshot.tracking.ahead == 1
    assert snapshot.tracking.behind == 1
