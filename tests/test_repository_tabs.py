"""Repository tabs retain isolated views and restore their session."""

from types import SimpleNamespace

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel, QTabBar, QWidget
from test_visible_workflows import git

from gitqt.app.bootstrap import build_services
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.main_window import MainWindow, MissingRepositoryView, _RepositoryTabs

pytest_plugins = ["test_visible_workflows"]


def _window(qtbot, config, monkeypatch):
    from gitqt.services import credentials

    monkeypatch.setattr(
        credentials, "CredentialBroker", lambda *args: SimpleNamespace(environment=lambda: {})
    )
    services = build_services(base_config_dir=config)
    services.worker_pool = ImmediateWorkerPool()
    window = MainWindow(services)
    qtbot.addWidget(window)
    return window


def test_repository_tabs_are_independent_and_restore_in_order(qtbot, repo, tmp_path, monkeypatch):
    second = tmp_path / "second"
    git(tmp_path, "init", str(second))
    git(second, "config", "user.name", "Test User")
    git(second, "config", "user.email", "test@example.com")
    (second / "file.txt").write_text("second\n")
    git(second, "add", "file.txt")
    git(second, "commit", "-m", "Second")
    config = tmp_path / "config"

    window = _window(qtbot, config, monkeypatch)
    window._activate_repository(str(repo))
    first_view = window._repository_view
    first_view._tabs.setCurrentIndex(1)
    window._activate_repository(str(second))
    second_view = window._repository_view
    assert window._tabs.count() == 2
    assert first_view is not second_view
    assert second_view._tabs.currentIndex() == 0
    window._persist_tabs()
    window.deleteLater()

    restored = _window(qtbot, config, monkeypatch)
    assert [restored._tab_path(index) for index in range(2)] == [str(repo), str(second)]
    assert restored._tabs.currentIndex() == 1
    assert restored._tabs.widget(0)._tabs.currentIndex() == 1


def test_missing_saved_repository_keeps_warning_tab(qtbot, tmp_path, monkeypatch):
    config = tmp_path / "config"
    services = build_services(base_config_dir=config)
    missing = str(tmp_path / "moved-repository")
    services.settings_store.update(open_repository_tabs=[missing], active_repository_tab=missing)

    window = _window(qtbot, config, monkeypatch)
    assert window._tabs.count() == 1
    assert isinstance(window._tabs.widget(0), MissingRepositoryView)
    assert window._tabs.tabText(0).startswith("⚠")
    assert "Repository no longer exists" in {
        label.text() for label in window._tabs.widget(0).findChildren(QLabel)
    }


def test_opening_same_repository_focuses_existing_tab(qtbot, repo, tmp_path, monkeypatch):
    window = _window(qtbot, tmp_path / "config", monkeypatch)
    window._activate_repository(str(repo))
    view = window._repository_view
    window._activate_repository(str(repo))
    assert window._tabs.count() == 1
    assert window._repository_view is view


def test_add_repository_button_sits_after_last_tab_without_covering_close(qtbot):
    tabs = _RepositoryTabs()
    qtbot.addWidget(tabs)
    tabs.setTabsClosable(True)
    tabs.resize(700, 400)
    tabs.addTab(QWidget(), "First repository")
    tabs.addTab(QWidget(), "Second repository")
    tabs.show()
    qtbot.wait(1)

    tab_bar = tabs.tabBar()
    expected_x = tab_bar.mapTo(
        tabs,
        QPoint(tab_bar.tabRect(1).right() + 1, 0),
    ).x()
    close_button = tab_bar.tabButton(1, QTabBar.ButtonPosition.RightSide)
    close_position = close_button.mapTo(tabs, QPoint(0, 0))

    assert tabs.add_button.x() == expected_x
    assert close_position.x() + close_button.width() <= tabs.add_button.x()
