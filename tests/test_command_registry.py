"""Shared desktop actions must keep keyboard, menu, and palette behavior aligned."""

from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu
from test_visible_workflows import git

from gitqt.app.bootstrap import build_services
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.commands import CommandPalette, CommandRegistry
from gitqt.ui.main_window import MainWindow

pytest_plugins = ["test_visible_workflows"]


def test_registry_rechecks_availability_before_stale_invocation(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    registry = CommandRegistry(window)
    state = {"available": True, "count": 0}

    def run():
        state["count"] += 1

    action = registry.register("run", "Run", run, available=lambda: state["available"])
    registry.refresh()
    assert action.isEnabled()
    state["available"] = False
    action.trigger()
    assert state["count"] == 0
    assert not action.isEnabled()
    state["available"] = True
    registry.refresh()
    action.trigger()
    assert state["count"] == 1


def test_palette_filters_commands_and_refuses_newly_unavailable_action(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    registry = CommandRegistry(window)
    calls = []
    state = {"ready": True}
    registry.register(
        "inspect",
        "Inspect files",
        lambda: calls.append("inspect"),
        available=lambda: state["ready"],
    )
    registry.register("other", "Other", lambda: calls.append("other"))
    palette = CommandPalette(registry, window)
    qtbot.addWidget(palette)
    palette.search.setText("inspect")
    assert palette.results.count() == 1
    state["ready"] = False
    palette._activate()
    assert calls == []
    state["ready"] = True
    palette._filter()
    palette._activate()
    assert calls == ["inspect"]


def make_window(qtbot, tmp_path, monkeypatch):
    from gitqt.services import credentials

    monkeypatch.setattr(
        credentials, "CredentialBroker", lambda *args: SimpleNamespace(environment=lambda: {})
    )
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    window = MainWindow(services)
    qtbot.addWidget(window)
    window.show()
    return window


def test_shared_menu_and_keyboard_stage_then_commit_real_changes(
    qtbot, repo, tmp_path, monkeypatch
):
    window = make_window(qtbot, tmp_path, monkeypatch)
    (repo / "file.txt").write_text("edited\n")
    window._activate_repository(str(repo))
    tree = window._repository_view._working_tree
    tree._unstaged_list.setCurrentRow(0)
    action = window._commands.action("stage")
    menu = next(m for m in window.findChildren(QMenu) if m.title() == "Working Tree")
    assert action in menu.actions()
    assert action in window.actions()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    qtbot.keyClick(
        window,
        Qt.Key.Key_S,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    qtbot.waitUntil(lambda: not tree._unstaged_list.count())
    assert git(repo, "diff", "--cached", "--name-only").stdout.strip() == b"file.txt"
    tree._summary_input.setText("Commit through a shared action")
    window._commands.action("commit").trigger()
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == b"Commit through a shared action"
    assert not git(repo, "status", "--porcelain").stdout.strip()


def test_repository_commands_disabled_on_dashboard_and_during_mutation(
    qtbot, repo, tmp_path, monkeypatch
):
    window = make_window(qtbot, tmp_path, monkeypatch)
    commands = window._commands
    assert not commands.action("compare").isEnabled()
    assert commands.action("preferences").isEnabled()
    window._activate_repository(str(repo))
    assert commands.action("compare").isEnabled()
    jobs = window._repository_view._jobs
    jobs.busy = True
    jobs.cancellable = True
    jobs.busy_changed.emit(True)
    assert not commands.action("compare").isEnabled()
    assert not commands.action("open").isEnabled()
    assert commands.action("cancel").isEnabled()
    jobs.busy = False
    jobs.busy_changed.emit(False)
    assert commands.action("compare").isEnabled()
    window._show_dashboard()
    assert not commands.action("compare").isEnabled()


def test_commit_shortcut_becomes_available_after_async_amend_preview(
    qtbot, repo, tmp_path, monkeypatch
):
    from test_session_jobs import DeferredPool

    window = make_window(qtbot, tmp_path, monkeypatch)
    window._activate_repository(str(repo))
    tree = window._repository_view._working_tree
    pool = DeferredPool()
    tree._jobs.pool = pool
    tree._amend_btn.setChecked(True)
    assert not tree._commit_btn.isEnabled()
    pool.finish(0)
    assert tree._commit_btn.isEnabled()
    assert window._commands.action("commit").isEnabled()


def test_menu_and_history_open_comparison_for_active_repository(qtbot, repo, tmp_path, monkeypatch):
    window = make_window(qtbot, tmp_path, monkeypatch)
    (repo / "file.txt").write_text("working changes\n")
    window._activate_repository(str(repo))
    window._commands.action("compare").trigger()
    view = window._repository_view
    dialog = view._comparison_dialogs[-1]
    assert dialog._repository_path == str(repo)
    assert dialog.files.count() == 1
    assert "working changes" in dialog.renderer.left.toPlainText()
    dialog.close()
    qtbot.waitUntil(lambda: not view._comparison_dialogs)

    view._tabs.setCurrentIndex(1)
    history = view._history_view
    expected = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    captured = []
    history.compare_commit_requested.connect(captured.append)

    def choose_inspect():
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        try:
            next(
                action
                for action in menu.actions()
                if action.text() == "Inspect commit and compare…"
            ).trigger()
        finally:
            menu.close()

    QTimer.singleShot(0, choose_inspect)
    index = history._proxy.index(0, 0)
    history._on_row_context_menu(history._table.visualRect(index).center())
    assert captured == [expected]
    inspected = view._comparison_dialogs[-1]
    assert inspected._comparison.commit.oid == expected
    assert inspected.parent_picker.currentData() == 0
    assert inspected.message.toPlainText() == "Initial"
    assert "one" in inspected.renderer.left.toPlainText()
    inspected.close()
    qtbot.waitUntil(lambda: not view._comparison_dialogs)
