"""Whole-file File Status actions preserve recoverability and intent."""

from pathlib import Path

from test_visible_workflows import git

from gitqt.services.status import StatusService

pytest_plugins = ["test_visible_workflows"]


def test_discard_files_restores_tracked_and_removes_untracked_with_backups(repo, runner):
    tracked = repo / "file.txt"
    tracked.write_text("edited\n")
    untracked = repo / "new.txt"
    untracked.write_text("new\n")
    service = StatusService(runner)

    backups = service.discard_files(repo, ["file.txt", "new.txt"])

    assert tracked.read_text() == "one\ntwo\nthree\n"
    assert not untracked.exists()
    assert len(backups) == 2
    assert all((Path(path) / "info.json").exists() for path in backups)


def test_ignore_rules_are_appended_once_and_left_unstaged(repo, runner):
    service = StatusService(runner)
    service.add_ignore_rule(repo, "build/output.log", rule_kind="filename")
    service.add_ignore_rule(repo, "build/output.log", rule_kind="extension")
    service.add_ignore_rule(repo, "build/output.log", rule_kind="path")
    service.add_ignore_rule(repo, "build/output.log", rule_kind="path")

    assert (repo / ".gitignore").read_text().splitlines() == [
        "output.log",
        "*.log",
        "/build/output.log",
    ]
    assert git(repo, "status", "--porcelain", ".gitignore").stdout.startswith(b"??")
