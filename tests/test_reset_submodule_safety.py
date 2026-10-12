"""Hard reset never discards nested work that GitQt cannot retain for recovery."""

import pytest
from test_visible_workflows import git

from gitqt.app.bootstrap import build_services
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.dialogs import ConfirmationDialog
from gitqt.ui.repository_view import RepositoryView

pytest_plugins = ["test_visible_workflows"]


@pytest.mark.parametrize(
    "replace_directory", [False, True], ids=["recurse-config", "path-collision"]
)
def test_reset_ui_refuses_unrecoverable_submodule_work(
    app, repo, tmp_path, monkeypatch, warnings, replace_directory
):
    source = tmp_path / "source"
    git(tmp_path, "init", "-b", "main", str(source))
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(source, "config", key, value)
    (source / "file.txt").write_text("committed child\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "Child commit")
    if replace_directory:
        (repo / "module").write_text("old regular file\n")
        git(repo, "add", "module")
        git(repo, "commit", "-qm", "Regular file at submodule path")
    target = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    if replace_directory:
        git(repo, "rm", "module")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(source), "module")
    git(repo, "commit", "-am", "Add submodule")
    git(repo, "config", "submodule.recurse", "true")
    child = repo / "module"
    (child / "file.txt").write_text("staged child\n")
    git(child, "add", "file.txt")
    (child / "file.txt").write_text("precious unstaged child\n")
    head = git(repo, "rev-parse", "HEAD").stdout
    child_index = git(child, "ls-files", "--stage", "-z").stdout
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    view = RepositoryView(services)
    view.load_repository(str(repo))
    monkeypatch.setattr(ConfirmationDialog, "exec", lambda *_: True)
    try:
        view._history_view.reset_to_commit_requested.emit(target, "hard")
        assert any("cannot yet back up submodule worktrees" in message for message in warnings)
        assert git(repo, "rev-parse", "HEAD").stdout == head
        assert git(child, "ls-files", "--stage", "-z").stdout == child_index
        assert (child / "file.txt").read_text() == "precious unstaged child\n"
        assert not view._jobs.busy
    finally:
        view._observe_timer.stop()
        view.close()
