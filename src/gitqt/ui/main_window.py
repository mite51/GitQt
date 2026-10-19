"""Main application window with dashboard and repository screen switching."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QStackedWidget,
)

from gitqt.app.bootstrap import AppServices
from gitqt.ui.commands import CommandPalette, CommandRegistry
from gitqt.ui.dashboard_view import DashboardView
from gitqt.ui.repository_view import RepositoryView


class MainWindow(QMainWindow):
    """Top-level window that switches between dashboard and repository views."""

    def __init__(self, services: AppServices) -> None:
        super().__init__()
        self._services = services
        from gitqt.services.credentials import CredentialBroker

        self._authentication_error = None
        try:
            services.credential_broker = CredentialBroker(self)
        except OSError as error:
            self._authentication_error = str(error)
        if services.git_runner is not None and services.credential_broker is not None:
            services.git_runner.credential_environment = services.credential_broker.environment
        self._active_repository_path: str | None = None

        self.setWindowTitle("GitQt")
        self.resize(1280, 800)
        self.setMinimumSize(QSize(960, 600))
        if services.settings_store.warning:
            self.statusBar().showMessage(services.settings_store.warning)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._dashboard = DashboardView(services)
        self._dashboard.repository_opened.connect(self._activate_repository)
        self._stack.addWidget(self._dashboard)

        self._repository_view = RepositoryView(services)
        self._repository_view.back_to_dashboard.connect(self._show_dashboard)
        self._stack.addWidget(self._repository_view)

        self._setup_shortcuts()
        self._setup_menu()
        self._restore_pending = True
        self._dashboard.ready.connect(self._restore_last_opened_repository)
        if self._dashboard.view_model.state.repositories:
            self._restore_last_opened_repository()
        QTimer.singleShot(0, self._check_setup)

    def _setup_shortcuts(self) -> None:
        self._commands = CommandRegistry(self)
        add = self._commands.register
        view = self._repository_view
        tree = view._working_tree

        def idle():
            return not view._jobs.busy

        def ready():
            return idle() and self._dialog_repository() is not None

        add(
            "open",
            "Open Repository…",
            self._dashboard._open_repository_dialog,
            available=idle,
            shortcut=QKeySequence.StandardKey.Open,
        )
        add("refresh", "Refresh", self._refresh_current, available=idle, shortcut="F5")
        add("preferences", "Preferences…", view._open_settings_dialog, available=idle)
        add("identity", "Commit Identity…", self._identity, available=idle)
        add("connections", "Connection Setup…", self._connections, available=idle)
        add("support", "Export Support Bundle…", self._export_diagnostics, available=idle)
        add("about", "About GitQt", self._about)
        add("palette", "Command Palette…", self._open_command_palette, shortcut="Ctrl+Shift+P")
        add("refs", "Branches and Tags…", view._open_ref_manager, available=ready)
        add("remotes", "Remotes…", view._open_remotes, available=ready)
        add("recovery", "Recovery…", view._open_recovery, available=ready)
        add("compare", "Compare Revisions…", view._open_comparison, available=ready)
        add(
            "working_tree",
            "Show Working Tree",
            lambda: view._tabs.setCurrentIndex(0),
            available=ready,
        )
        add("history", "Show History", lambda: view._tabs.setCurrentIndex(1), available=ready)
        add(
            "compose",
            "Focus Commit Message",
            self._focus_composer,
            available=ready,
            shortcut="Ctrl+Alt+C",
        )
        add(
            "commit",
            "Commit",
            tree._commit_btn.click,
            available=lambda: ready() and tree._commit_btn.isEnabled(),
            shortcut="Ctrl+Return",
        )
        add(
            "stage",
            "Stage Selected Files",
            tree._stage_btn.click,
            available=lambda: ready() and bool(tree._unstaged_list.selectedItems()),
            shortcut="Ctrl+Shift+S",
        )
        add(
            "unstage",
            "Unstage Selected Files",
            tree._unstage_btn.click,
            available=lambda: ready() and bool(tree._staged_list.selectedItems()),
            shortcut="Ctrl+Shift+U",
        )
        add(
            "cancel",
            "Cancel Current Operation",
            view._jobs.cancel,
            available=lambda: view._jobs.busy and view._jobs.cancellable,
        )
        view._jobs.busy_changed.connect(self._commands.refresh)
        tree._staged_list.itemSelectionChanged.connect(self._commands.refresh)
        tree._unstaged_list.itemSelectionChanged.connect(self._commands.refresh)
        tree._summary_input.textChanged.connect(self._commands.refresh)
        tree.working_tree_changed.connect(self._commands.refresh)
        tree.commit_succeeded.connect(self._commands.refresh)
        tree.command_state_changed.connect(self._commands.refresh)

    def _setup_menu(self):
        application = self.menuBar().addMenu("Application")
        for key in (
            "open",
            "preferences",
            "identity",
            "connections",
            "support",
            "palette",
            "about",
        ):
            application.addAction(self._commands.action(key))
        repository = self.menuBar().addMenu("Repository")
        for key in ("refresh", "refs", "remotes", "compare", "recovery", "cancel"):
            repository.addAction(self._commands.action(key))
        workflow = self.menuBar().addMenu("Working Tree")
        for key in ("working_tree", "history", "compose", "stage", "unstage", "commit"):
            workflow.addAction(self._commands.action(key))
        for menu in (application, repository, workflow):
            menu.aboutToShow.connect(self._commands.refresh)
        self._commands.refresh()

    def _open_command_palette(self):
        CommandPalette(self._commands, self).exec()

    def _focus_composer(self):
        self._repository_view._tabs.setCurrentIndex(0)
        self._repository_view._working_tree._summary_input.setFocus()

    def _about(self):
        from gitqt import __version__

        QMessageBox.about(
            self,
            "About GitQt",
            f"GitQt {__version__}\nDesktop Git client — GPL-3.0-or-later\nRequires Git 2.30 or"
            " newer, installed separately.\nUpdates: install a newer verified distribution over"
            " the application; settings are stored separately.\nRelease notes and third-party"
            " notices accompany each distribution.",
        )

    def _export_diagnostics(self):
        from PySide6.QtWidgets import QFileDialog

        from gitqt.services.diagnostics import export_support_bundle

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export version information and sanitized application logs",
            "GitQt-support.zip",
            "ZIP archives (*.zip)",
        )
        if path:
            self._repository_view._jobs.write(
                lambda: export_support_bundle(self._services, path),
                lambda _: QMessageBox.information(
                    self,
                    "Support Bundle",
                    "Saved locally. Review its contents before sharing; log messages may contain"
                    " local paths.",
                ),
                lambda error: QMessageBox.warning(self, "Export Failed", str(error)),
            )

    def _dialog_repository(self):
        return (
            self._active_repository_path
            if self._stack.currentWidget() is self._repository_view
            else None
        )

    def _identity(self):
        if self._services.git_runner is None:
            self._repository_view._open_settings_dialog()
            return
        from gitqt.ui.setup_dialogs import IdentityDialog

        IdentityDialog(self._services, self._dialog_repository(), self).exec()
        self._repository_view._refresh_user_chip()

    def _connections(self):
        if self._services.git_runner is None:
            self._repository_view._open_settings_dialog()
            return
        from gitqt.ui.setup_dialogs import ConnectionDialog

        ConnectionDialog(self._services, self._dialog_repository(), self).exec()

    def _check_setup(self):
        if self._authentication_error:
            self.statusBar().showMessage(
                "Desktop credential prompts are unavailable: "
                + self._authentication_error
                + ". Existing Git credential helpers can still work."
            )
            return
        if self._services.git_runner is None:
            self.statusBar().showMessage(
                "Git is unavailable. Open Application → Preferences to choose a Git executable."
            )
            return

        def apply(version):
            import re

            match = re.search(r"git version (\d+)\.(\d+)", version.stdout)
            if match is None or tuple(map(int, match.groups())) < (2, 30):
                self.statusBar().showMessage(
                    "Git 2.30 or newer is required. Open Application → Preferences."
                )
            else:
                self.statusBar().showMessage(
                    "Ready. Set your name and email in Application → Commit Identity; configure"
                    " private access in Connection Setup."
                )

        self._repository_view._jobs.read(
            "setup",
            lambda: self._services.git_runner.run(["--version"], timeout_seconds=10),
            apply,
            lambda error: self.statusBar().showMessage(
                f"Git setup failed: {error}. Open Application → Preferences."
            ),
        )

    def _activate_repository(self, repository_path: str) -> None:
        resolved = str(Path(repository_path).expanduser().resolve())
        if not self._repository_view.load_repository(resolved):
            return
        self._active_repository_path = resolved
        repo_name = Path(resolved).name or resolved
        self.setWindowTitle(f"GitQt — {repo_name}")
        self._stack.setCurrentWidget(self._repository_view)
        self._commands.refresh()

    def _show_dashboard(self) -> None:
        if self._repository_view._jobs.busy:
            return
        if not self._repository_view._working_tree.flush_draft():
            return
        if not self._repository_view._jobs.activate(None):
            return
        self._repository_view._history_view.invalidate_jobs()
        self.setWindowTitle("GitQt")
        self._dashboard.refresh()
        self._stack.setCurrentWidget(self._dashboard)
        self._commands.refresh()

    def _refresh_current(self) -> None:
        if self._stack.currentWidget() is self._repository_view and self._active_repository_path:
            self._repository_view.load_repository(self._active_repository_path)
        else:
            self._dashboard.refresh()

    def _restore_last_opened_repository(self) -> None:
        if not self._restore_pending:
            return
        self._restore_pending = False
        for repo in self._dashboard.view_model.state.repositories:
            if repo.is_last_opened and repo.validation.is_valid:
                self._activate_repository(repo.path)
                return

    def closeEvent(self, event) -> None:
        from gitqt.services.session_jobs import SessionJobs

        if any(jobs.busy for jobs in self.findChildren(SessionJobs)):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                self, "Operation Running", "Wait for the operation to finish before closing GitQt."
            )
            event.ignore()
            return
        if not self._repository_view._working_tree.flush_draft():
            event.ignore()
            return
        self._repository_view._jobs.activate(None)
        self._repository_view._history_view.invalidate_jobs()
        super().closeEvent(event)
