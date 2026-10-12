"""First-run setup remains accessible before a repository can be opened."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMenu

from gitqt.app.bootstrap import build_services
from gitqt.services.git import GitCommandResult
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.main_window import MainWindow


@pytest.mark.parametrize("state", ["missing", "old"])
def test_first_run_reports_unusable_git_and_exposes_setup(qtbot, tmp_path, monkeypatch, state):
    from gitqt.services import credentials

    # Authentication is exercised independently; this setup check needs no socket.
    monkeypatch.setattr(
        credentials,
        "CredentialBroker",
        lambda *args: SimpleNamespace(environment=lambda: {}),
    )
    kwargs = {"explicit_git_path": str(tmp_path / "missing-git")} if state == "missing" else {}
    services = build_services(base_config_dir=tmp_path / "settings", **kwargs)
    services.worker_pool = ImmediateWorkerPool()
    if state == "old":
        original = services.git_runner.run

        def run(args, **kwargs):
            if args == ["--version"]:
                return GitCommandResult(tuple(args), "git version 2.29.0", "", 0, 1)
            return original(args, **kwargs)

        monkeypatch.setattr(services.git_runner, "run", run)
    window = MainWindow(services)
    qtbot.addWidget(window)
    window.show()
    expected = "Git is unavailable" if state == "missing" else "Git 2.30 or newer is required"
    qtbot.waitUntil(lambda: expected in window.statusBar().currentMessage())
    application_menu = next(
        menu for menu in window.findChildren(QMenu) if menu.title() == "Application"
    )
    actions = application_menu.actions()
    names = {action.text(): action for action in actions}
    for name in ("Preferences…", "Commit Identity…", "Connection Setup…"):
        assert names[name].isEnabled()
    assert "Application → Preferences" in window.statusBar().currentMessage()
