"""Remote choices exercised through real dialogs against disposable local remotes."""

import pytest
from PySide6.QtWidgets import QMessageBox
from test_visible_workflows import git

from gitqt.app.bootstrap import build_services
from gitqt.services.recovery import RecoveryService
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.remote_dialogs import RemotesDialog, TransferDialog
from gitqt.ui.repository_view import RepositoryView

pytest_plugins = ["test_visible_workflows"]


def services_at(tmp_path):
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    return services


@pytest.fixture
def remote(repo, tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-u", "origin", "main")
    return bare


def test_remote_dialog_add_edit_remove_retains_recovery(app, repo, tmp_path, monkeypatch):
    services = services_at(tmp_path)
    bare = tmp_path / "new.git"
    git(tmp_path, "init", "--bare", str(bare))
    dialog = RemotesDialog(services, str(repo))
    try:
        dialog.name.setText("origin")
        dialog.fetch.setText(str(bare))
        dialog.save.click()
        assert git(repo, "remote", "get-url", "origin").stdout.decode().strip() == str(bare)
        dialog.choice.setCurrentText("origin")
        dialog.name.setText("upstream")
        dialog.save.click()
        assert git(repo, "remote").stdout == b"upstream\n"
        dialog.choice.setCurrentText("upstream")
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        dialog.remove.click()
        assert not git(repo, "remote").stdout.strip()
        assert any(
            item.kind == "remote" for item in RecoveryService(services.git_runner).entries(repo)
        )
        assert dialog.save.isEnabled() and not dialog.jobs.busy
    finally:
        dialog.close()


def test_first_publish_dialog_uses_selected_remote_and_tracks_branch(
    app, repo, tmp_path, monkeypatch, warnings
):
    first, second = tmp_path / "upstream.git", tmp_path / "fork.git"
    for name, path in (("upstream", first), ("fork", second)):
        git(tmp_path, "init", "--bare", str(path))
        git(repo, "remote", "add", name, str(path))
    view = RepositoryView(services_at(tmp_path))
    view.load_repository(str(repo))

    def accept(dialog):
        dialog.remote.setCurrentText("fork")
        dialog.branch.setText("published-topic")
        assert dialog.option.isChecked()
        assert str(second) in dialog.destination.text()
        dialog.go.click()
        return dialog.result()

    monkeypatch.setattr(TransferDialog, "exec", accept)
    try:
        view._push_btn.click()
        assert (
            git(second, "rev-parse", "published-topic").stdout
            == git(repo, "rev-parse", "HEAD").stdout
        )
        assert git(first, "show-ref", check=False).returncode != 0
        assert (
            git(repo, "rev-parse", "--abbrev-ref", "@{upstream}").stdout
            == b"fork/published-topic\n"
        )
        assert not warnings and not view._jobs.busy
    finally:
        view._observe_timer.stop()
        view.close()


def test_fetch_dialog_prunes_stale_tracking_branch(
    app, repo, remote, tmp_path, monkeypatch, warnings
):
    git(repo, "push", "origin", "HEAD:removed")
    git(repo, "fetch", "origin")
    git(remote, "update-ref", "-d", "refs/heads/removed")
    view = RepositoryView(services_at(tmp_path))
    view.load_repository(str(repo))

    def accept(dialog):
        dialog.option.setChecked(True)
        dialog.go.click()
        return dialog.result()

    monkeypatch.setattr(TransferDialog, "exec", accept)
    try:
        view._fetch_btn.click()
        assert (
            git(repo, "show-ref", "--verify", "refs/remotes/origin/removed", check=False).returncode
            != 0
        )
        assert not warnings and not view._jobs.busy
    finally:
        view._observe_timer.stop()
        view.close()


@pytest.mark.parametrize("strategy", [0, 1, 2], ids=["ff-only", "merge", "rebase"])
def test_pull_dialog_obeys_selected_strategy(
    app, repo, remote, tmp_path, monkeypatch, warnings, strategy
):
    other = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(other))
    for name, value in (
        ("user.name", "Other"),
        ("user.email", "other@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        git(other, "config", name, value)
    (other / "remote.txt").write_text("remote change\n")
    git(other, "add", ".")
    git(other, "commit", "-qm", "Remote change")
    git(other, "push", "origin", "main")
    remote_head = git(other, "rev-parse", "HEAD").stdout.decode().strip()
    (repo / "local.txt").write_text("local change\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Local change")
    original = git(repo, "rev-parse", "HEAD").stdout
    view = RepositoryView(services_at(tmp_path))
    view.load_repository(str(repo))

    def accept(dialog):
        dialog.strategy.setCurrentIndex(strategy)
        dialog.go.click()
        return dialog.result()

    monkeypatch.setattr(TransferDialog, "exec", accept)
    monkeypatch.setenv("GIT_MERGE_AUTOEDIT", "no")
    try:
        view._pull_btn.click()
        parents = git(repo, "rev-list", "--parents", "-1", "HEAD").stdout.split()
        if strategy == 0:
            assert git(repo, "rev-parse", "HEAD").stdout == original
            assert warnings
        else:
            assert not warnings
            assert (repo / "remote.txt").read_text() == "remote change\n"
            assert (repo / "local.txt").read_text() == "local change\n"
            assert len(parents) == (3 if strategy == 1 else 2)
            assert git(repo, "merge-base", "--is-ancestor", remote_head, "HEAD").returncode == 0
        assert not view._jobs.busy
    finally:
        view._observe_timer.stop()
        view.close()
