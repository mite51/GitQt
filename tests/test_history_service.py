from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitqt.services.git import GitLocator, GitNotFoundError, GitRunner
from gitqt.services.history import HistoryService


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


def _create_history_service() -> HistoryService:
    locator = GitLocator()
    try:
        executable = locator.discover()
    except GitNotFoundError as exc:
        pytest.skip(str(exc))
    return HistoryService(GitRunner(executable))


def test_history_service_returns_graph_rows_for_branching_layout(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "Test User")
    _run_git(repo_path, "config", "user.email", "test@example.com")

    tracked = repo_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Base commit")
    _run_git(repo_path, "branch", "-M", "main")

    _run_git(repo_path, "checkout", "-b", "feature/history")
    feature_only = repo_path / "feature.txt"
    feature_only.write_text("feature line\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "feature.txt")
    _run_git(repo_path, "commit", "-m", "Feature commit")
    _run_git(repo_path, "checkout", "main")

    main_only = repo_path / "main.txt"
    main_only.write_text("main line\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "main.txt")
    _run_git(repo_path, "commit", "-m", "Main commit")
    _run_git(repo_path, "merge", "--no-ff", "feature/history", "-m", "Merge feature/history")

    service = _create_history_service()
    rows = service.get_graph(repo_path, limit=20)

    assert len(rows) >= 4
    assert rows[0].head_ref == "main"
    assert any(row.summary == "Merge feature/history" for row in rows)
    assert any(row.graph_prefix for row in rows)

    merge_row = next(row for row in rows if row.summary == "Merge feature/history")
    assert len(merge_row.parents) == 2
    assert all(row.author_email == "test@example.com" for row in rows)
    assert all(row.age for row in rows)
    assert all(len(row.commit_full_hash) == 40 for row in rows)


def test_history_service_refs_filter_narrows_log(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "Test User")
    _run_git(repo_path, "config", "user.email", "test@example.com")

    tracked = repo_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "tracked.txt")
    _run_git(repo_path, "commit", "-m", "Base commit")
    _run_git(repo_path, "branch", "-M", "main")

    _run_git(repo_path, "checkout", "-b", "feature/only")
    feature_file = repo_path / "feature.txt"
    feature_file.write_text("feature\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "feature.txt")
    _run_git(repo_path, "commit", "-m", "Feature only commit")
    _run_git(repo_path, "checkout", "main")

    service = _create_history_service()

    all_rows = service.get_graph(repo_path, limit=10)
    main_rows = service.get_graph(repo_path, limit=10, refs=["main"])

    summaries_all = {row.summary for row in all_rows}
    summaries_main_only = {row.summary for row in main_rows}

    assert "Feature only commit" in summaries_all
    assert "Feature only commit" not in summaries_main_only
    assert "Base commit" in summaries_main_only


def test_history_service_get_commit_files_reports_add_modify_delete(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.name", "Test User")
    _run_git(repo_path, "config", "user.email", "test@example.com")

    a = repo_path / "a.txt"
    a.write_text("one\n", encoding="utf-8")
    b = repo_path / "b.txt"
    b.write_text("two\n", encoding="utf-8")
    _run_git(repo_path, "add", "--", "a.txt", "b.txt")
    _run_git(repo_path, "commit", "-m", "Base")

    # Commit 2: modify a, add c, delete b.
    a.write_text("one-edit\n", encoding="utf-8")
    c = repo_path / "c.txt"
    c.write_text("three\n", encoding="utf-8")
    _run_git(repo_path, "rm", "--", "b.txt")
    _run_git(repo_path, "add", "--", "a.txt", "c.txt")
    _run_git(repo_path, "commit", "-m", "Mixed changes")

    sha = _run_git(repo_path, "rev-parse", "HEAD").strip()

    service = _create_history_service()
    files = service.get_commit_files(repo_path, sha)

    status_map = {change.path: change.status for change in files}
    assert status_map.get("a.txt") == "M"
    assert status_map.get("c.txt") == "A"
    assert status_map.get("b.txt") == "D"
