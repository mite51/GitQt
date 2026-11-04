"""Main application window with persistent, independent repository tabs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gitqt.app.bootstrap import AppServices
from gitqt.services.session_jobs import SessionJobs
from gitqt.ui.add_repository_dialog import AddRepositoryDialog
from gitqt.ui.commands import CommandPalette, CommandRegistry
from gitqt.ui.repository_view import RepositoryView
from gitqt.ui.theme import ui_font


class _RepositoryTabs(QTabWidget):
    add_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tabBar().setExpanding(False)
        self.tabBar().setUsesScrollButtons(True)
        self.tabBar().setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self.add_button = QToolButton(self)
        self.add_button.setText("+")
        self.add_button.setToolTip("Add repository")
        self.add_button.setAutoRaise(True)
        self.add_button.setFixedSize(30, 28)
        self.add_button.clicked.connect(self.add_requested)

    def reposition_add_button(self) -> None:
        """Keep add beside the final tab while retaining overflow space."""
        tab_bar = self.tabBar()
        x = tab_bar.geometry().right() + 1 if self.count() else tab_bar.geometry().left()
        self.add_button.move(x, 2)
        self.add_button.raise_()

    def _queue_add_button_position(self) -> None:
        QTimer.singleShot(0, self.reposition_add_button)

    def resizeEvent(self, event) -> None:
        self.tabBar().setMaximumWidth(max(0, event.size().width() - self.add_button.width() - 6))
        super().resizeEvent(event)
        self._queue_add_button_position()

    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        self._queue_add_button_position()

    def tabRemoved(self, index: int) -> None:
        super().tabRemoved(index)
        self._queue_add_button_position()


class MissingRepositoryView(QWidget):
    locate_requested = Signal(str)
    retry_requested = Signal(str)
    remove_requested = Signal()

    def __init__(self, repository_path: str, parent=None) -> None:
        super().__init__(parent)
        self.repository_path = repository_path
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)
        icon = QLabel("⚠")
        icon.setFont(ui_font(34))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        title = QLabel("Repository no longer exists")
        title.setFont(ui_font(16, bold=True))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        path = QLabel(repository_path)
        path.setProperty("dim", True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(path)
        buttons = QHBoxLayout()
        locate = QPushButton("Locate Repository…")
        locate.clicked.connect(lambda: self.locate_requested.emit(self.repository_path))
        buttons.addWidget(locate)
        retry = QPushButton("Retry")
        retry.clicked.connect(lambda: self.retry_requested.emit(self.repository_path))
        buttons.addWidget(retry)
        remove = QPushButton("Remove Tab")
        remove.clicked.connect(self.remove_requested)
        buttons.addWidget(remove)
        layout.addLayout(buttons)


class MainWindow(QMainWindow):
    """Top-level tabbed repository workspace."""

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
        self._repository_view: RepositoryView | None = None
        self._setup_jobs = SessionJobs(services.worker_pool, self)
        self._restoring = True

        self.setWindowTitle("GitQt")
        self.resize(1280, 800)
        self.setMinimumSize(QSize(960, 600))
        if services.settings_store.warning:
            self.statusBar().showMessage(services.settings_store.warning)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self._empty = self._build_empty_view()
        self._stack.addWidget(self._empty)

        self._tabs = _RepositoryTabs()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._add_repository_btn = self._tabs.add_button
        self._tabs.add_requested.connect(self._show_add_repository_dialog)
        self._tabs.tabCloseRequested.connect(self._close_repository_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.tabBar().tabMoved.connect(lambda *_: self._persist_tabs())
        self._stack.addWidget(self._tabs)

        self._setup_shortcuts()
        self._setup_menu()
        self._restore_repository_tabs()
        self._restoring = False
        self._update_empty_state()
        QTimer.singleShot(0, self._check_setup)

    def _build_empty_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)
        icon = QLabel("⌘")
        icon.setFont(ui_font(36, bold=True))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        title = QLabel("No repositories open")
        title.setFont(ui_font(18, bold=True))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("Add a repository to start working.")
        hint.setProperty("dim", True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        button = QPushButton("Add New Repository")
        button.setProperty("accent", True)
        button.clicked.connect(self._show_add_repository_dialog)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        return view

    def _active_view(self) -> RepositoryView | None:
        if self._stack.currentWidget() is not self._tabs:
            return None
        widget = self._tabs.currentWidget()
        return widget if isinstance(widget, RepositoryView) else None

    def _active_tree(self):
        view = self._active_view()
        return view._working_tree if view is not None else None

    def _idle(self) -> bool:
        view = self._active_view()
        return view is None or not view._jobs.busy

    def _ready(self) -> bool:
        return self._active_view() is not None and self._idle()

    def _setup_shortcuts(self) -> None:
        self._commands = CommandRegistry(self)
        add = self._commands.register
        add(
            "open",
            "Add Repository…",
            self._show_add_repository_dialog,
            available=self._idle,
            shortcut=QKeySequence.StandardKey.Open,
        )
        add("refresh", "Refresh", self._refresh_current, available=self._ready, shortcut="F5")
        add("preferences", "Preferences…", self._preferences, available=self._idle)
        add("identity", "Commit Identity…", self._identity, available=self._idle)
        add("connections", "Connection Setup…", self._connections, available=self._idle)
        add("support", "Export Support Bundle…", self._export_diagnostics, available=self._idle)
        add("about", "About GitQt", self._about)
        add("palette", "Command Palette…", self._open_command_palette, shortcut="Ctrl+Shift+P")
        add("refs", "Branches and Tags…", self._open_refs, available=self._ready)
        add("remotes", "Remotes…", self._open_remotes, available=self._ready)
        add("recovery", "Recovery…", self._open_recovery, available=self._ready)
        add("compare", "Compare Revisions…", self._open_comparison, available=self._ready)
        add("working_tree", "Show File Status", lambda: self._set_mode(0), available=self._ready)
        add("history", "Show History", lambda: self._set_mode(1), available=self._ready)
        add(
            "compose",
            "Focus Commit Message",
            self._focus_composer,
            available=self._ready,
            shortcut="Ctrl+Alt+C",
        )
        add("commit", "Commit", self._commit, available=self._can_commit, shortcut="Ctrl+Return")
        add(
            "stage",
            "Stage Selected Files",
            self._stage,
            available=self._can_stage,
            shortcut="Ctrl+Shift+S",
        )
        add(
            "unstage",
            "Unstage Selected Files",
            self._unstage,
            available=self._can_unstage,
            shortcut="Ctrl+Shift+U",
        )
        add("cancel", "Cancel Current Operation", self._cancel, available=self._can_cancel)

    def _setup_menu(self) -> None:
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
        workflow = self.menuBar().addMenu("File Status")
        for key in ("working_tree", "history", "compose", "stage", "unstage", "commit"):
            workflow.addAction(self._commands.action(key))
        for menu in (application, repository, workflow):
            menu.aboutToShow.connect(self._commands.refresh)
        self._commands.refresh()

    def _connect_view(self, view: RepositoryView) -> None:
        tree = view._working_tree
        view._jobs.busy_changed.connect(self._commands.refresh)
        view.tab_metadata_changed.connect(lambda: self._refresh_tab_metadata(view))
        tree._staged_list.itemSelectionChanged.connect(self._commands.refresh)
        tree._unstaged_list.itemSelectionChanged.connect(self._commands.refresh)
        tree._summary_input.textChanged.connect(self._commands.refresh)
        tree.working_tree_changed.connect(self._commands.refresh)
        tree.commit_succeeded.connect(self._commands.refresh)
        tree.command_state_changed.connect(self._commands.refresh)

    def _show_add_repository_dialog(self) -> None:
        dialog = AddRepositoryDialog(self._services, self)
        if dialog.exec() and dialog.result_path():
            self._activate_repository(dialog.result_path())

    def _activate_repository(self, repository_path: str) -> None:
        resolved = str(Path(repository_path).expanduser().resolve())
        existing = self._index_for_path(resolved)
        if existing >= 0:
            self._update_empty_state()
            self._tabs.setCurrentIndex(existing)
            self._flash_tab(existing)
            return
        view = RepositoryView(self._services)
        self._connect_view(view)
        if not view.load_repository(resolved):
            view.deleteLater()
            return
        index = self._tabs.addTab(view, Path(resolved).name or resolved)
        self._tabs.setTabToolTip(index, resolved)
        saved = self._services.settings.repository_view_states.get(resolved, {})
        view.restore_view_state(saved)
        self._tabs.setCurrentIndex(index)
        self._update_empty_state()
        self._on_tab_changed(index)
        self._persist_tabs()

    def _restore_repository_tabs(self) -> None:
        settings = self._services.settings
        for path in settings.open_repository_tabs:
            resolved = str(Path(path).expanduser().resolve())
            if Path(resolved).is_dir():
                self._activate_repository(resolved)
            else:
                self._add_missing_repository(resolved)
        active = settings.active_repository_tab
        if active:
            index = self._index_for_path(str(Path(active).expanduser().resolve()))
            if index >= 0:
                self._tabs.setCurrentIndex(index)

    def _add_missing_repository(self, path: str, index: int | None = None) -> None:
        view = MissingRepositoryView(path)
        view.locate_requested.connect(lambda _old: self._locate_missing(view))
        view.retry_requested.connect(lambda old: self._replace_missing(view, old))
        view.remove_requested.connect(lambda: self._close_repository_tab(self._tabs.indexOf(view)))
        target = self._tabs.count() if index is None else index
        self._tabs.insertTab(target, view, f"⚠  {Path(path).name or path}")
        self._tabs.setTabToolTip(target, path)

    def _locate_missing(self, view: MissingRepositoryView) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Locate Repository", str(Path.home()))
        if directory:
            self._replace_missing(view, directory)

    def _replace_missing(self, view: MissingRepositoryView, path: str) -> None:
        if self._tabs.indexOf(view) < 0:
            return

        def apply(validation) -> None:
            if not validation.is_valid:
                QMessageBox.warning(self, "Repository Not Found", validation.message)
                return
            current_index = self._tabs.indexOf(view)
            if current_index < 0:
                return
            self._tabs.removeTab(current_index)
            view.deleteLater()
            self._activate_repository(validation.path)

        self._setup_jobs.write(
            lambda: self._services.repositories.open_repository(path),
            apply,
            lambda error: QMessageBox.warning(self, "Repository Not Found", str(error)),
        )

    def _index_for_path(self, path: str) -> int:
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            current = getattr(widget, "repository_path", None)
            if current == path:
                return index
        return -1

    def _tab_path(self, index: int) -> str | None:
        return getattr(self._tabs.widget(index), "repository_path", None)

    def _close_repository_tab(self, index: int) -> None:
        widget = self._tabs.widget(index)
        if widget is None:
            return
        if isinstance(widget, RepositoryView) and widget._jobs.busy:
            QMessageBox.information(
                self,
                "Operation Running",
                "Wait for the repository operation to finish before closing this tab.",
            )
            return
        path = self._tab_path(index) or "repository"
        name = Path(path).name or path
        answer = QMessageBox.question(
            self,
            "Close Repository",
            f"Closing {name}. Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if isinstance(widget, RepositoryView):
            if not widget._working_tree.flush_draft():
                return
            widget._history_view.invalidate_jobs()
            widget._jobs.activate(None)
        self._tabs.removeTab(index)
        widget.deleteLater()
        self._update_empty_state()
        self._persist_tabs()

    def _on_tab_changed(self, _index: int) -> None:
        view = self._active_view()
        self._repository_view = view
        self._active_repository_path = view.repository_path if view else None
        self.setWindowTitle(f"GitQt — {Path(view.repository_path).name}" if view else "GitQt")
        self._commands.refresh()
        if not self._restoring:
            self._persist_tabs()

    def _refresh_tab_metadata(self, view: RepositoryView) -> None:
        index = self._tabs.indexOf(view)
        if index < 0:
            return
        name = Path(view.repository_path).name or view.repository_path
        self._tabs.setTabText(index, name)
        self._tabs.setTabToolTip(index, f"{view.repository_path}\nBranch: {view.active_branch}")
        self._tabs._queue_add_button_position()

    def _flash_tab(self, index: int) -> None:
        widget = self._tabs.widget(index)
        original = self._tabs.tabText(index)
        self._tabs.setTabText(index, f"●  {original}")
        self._tabs._queue_add_button_position()

        def restore() -> None:
            current = self._tabs.indexOf(widget)
            if current >= 0:
                self._tabs.setTabText(current, original)
                self._tabs._queue_add_button_position()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(restore)
        timer.timeout.connect(timer.deleteLater)
        timer.start(700)

    def _update_empty_state(self) -> None:
        self._stack.setCurrentWidget(self._tabs if self._tabs.count() else self._empty)

    def _persist_tabs(self) -> None:
        if self._restoring:
            return
        paths = [path for i in range(self._tabs.count()) if (path := self._tab_path(i))]
        active = self._tab_path(self._tabs.currentIndex()) if self._tabs.count() else None
        states = dict(self._services.settings.repository_view_states)
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            if isinstance(widget, RepositoryView):
                states[widget.repository_path] = widget.export_view_state()

        def update(settings) -> None:
            settings.open_repository_tabs = paths
            settings.active_repository_tab = active
            settings.repository_view_states = states

        self._services.settings = self._services.settings_store.mutate(update)

    def _set_mode(self, index: int) -> None:
        view = self._active_view()
        if view:
            view._tabs.setCurrentIndex(index)
            self._persist_tabs()

    def _can_commit(self) -> bool:
        tree = self._active_tree()
        return self._ready() and tree is not None and tree._commit_btn.isEnabled()

    def _can_stage(self) -> bool:
        tree = self._active_tree()
        return self._ready() and tree is not None and bool(tree._unstaged_list.selectedItems())

    def _can_unstage(self) -> bool:
        tree = self._active_tree()
        return self._ready() and tree is not None and bool(tree._staged_list.selectedItems())

    def _can_cancel(self) -> bool:
        view = self._active_view()
        return bool(view and view._jobs.busy and view._jobs.cancellable)

    def _commit(self) -> None:
        tree = self._active_tree()
        if tree:
            tree._commit_btn.click()

    def _stage(self) -> None:
        tree = self._active_tree()
        if tree:
            tree._stage_btn.click()

    def _unstage(self) -> None:
        tree = self._active_tree()
        if tree:
            tree._unstage_btn.click()

    def _cancel(self) -> None:
        view = self._active_view()
        if view:
            view._jobs.cancel()

    def _open_refs(self) -> None:
        view = self._active_view()
        if view:
            view._open_ref_manager()

    def _open_remotes(self) -> None:
        view = self._active_view()
        if view:
            view._open_remotes()

    def _open_recovery(self) -> None:
        view = self._active_view()
        if view:
            view._open_recovery()

    def _open_comparison(self) -> None:
        view = self._active_view()
        if view:
            view._open_comparison()

    def _open_command_palette(self) -> None:
        CommandPalette(self._commands, self).exec()

    def _focus_composer(self) -> None:
        view = self._active_view()
        if view:
            view._tabs.setCurrentIndex(0)
            view._working_tree._summary_input.setFocus()

    def _preferences(self) -> None:
        view = self._active_view()
        if view:
            view._open_settings_dialog()
            return
        from gitqt.ui.settings_dialog import SettingsDialog

        SettingsDialog(self._services, self).exec()

    def _dialog_repository(self) -> str | None:
        return self._active_repository_path

    def _identity(self) -> None:
        if self._services.git_runner is None:
            self._preferences()
            return
        from gitqt.ui.setup_dialogs import IdentityDialog

        IdentityDialog(self._services, self._dialog_repository(), self).exec()
        view = self._active_view()
        if view:
            view._refresh_user_chip()

    def _connections(self) -> None:
        if self._services.git_runner is None:
            self._preferences()
            return
        from gitqt.ui.setup_dialogs import ConnectionDialog

        ConnectionDialog(self._services, self._dialog_repository(), self).exec()

    def _about(self) -> None:
        from gitqt import __version__

        QMessageBox.about(
            self,
            "About GitQt",
            f"GitQt {__version__}\nDesktop Git client — GPL-3.0-or-later\n"
            "Requires Git 2.30 or newer.",
        )

    def _export_diagnostics(self) -> None:
        from gitqt.services.diagnostics import export_support_bundle

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export version information and sanitized application logs",
            "GitQt-support.zip",
            "ZIP archives (*.zip)",
        )
        if path:
            self._setup_jobs.write(
                lambda: export_support_bundle(self._services, path),
                lambda _: QMessageBox.information(self, "Support Bundle", "Support bundle saved."),
                lambda error: QMessageBox.warning(self, "Export Failed", str(error)),
            )

    def _refresh_current(self) -> None:
        view = self._active_view()
        if view:
            view.load_repository(view.repository_path)

    def _show_dashboard(self) -> None:
        """Compatibility helper for callers that previously navigated to the dashboard."""
        self._stack.setCurrentWidget(self._empty)
        self._repository_view = None
        self._active_repository_path = None
        self._commands.refresh()

    def _check_setup(self) -> None:
        if self._authentication_error:
            self.statusBar().showMessage(
                "Desktop credential prompts are unavailable: " + self._authentication_error
            )
            return
        if self._services.git_runner is None:
            self.statusBar().showMessage(
                "Git is unavailable. Open Application → Preferences to choose a Git executable."
            )
            return

        def apply(version) -> None:
            import re

            match = re.search(r"git version (\d+)\.(\d+)", version.stdout)
            if match is None or tuple(map(int, match.groups())) < (2, 30):
                self.statusBar().showMessage(
                    "Git 2.30 or newer is required. Open Application → Preferences."
                )
            else:
                self.statusBar().showMessage("Ready")

        self._setup_jobs.read(
            "setup",
            lambda: self._services.git_runner.run(["--version"], timeout_seconds=10),
            apply,
            lambda error: self.statusBar().showMessage(f"Git setup failed: {error}"),
        )

    def closeEvent(self, event) -> None:
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            if isinstance(widget, RepositoryView) and not widget._working_tree.flush_draft():
                event.ignore()
                return
        self._persist_tabs()
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            if isinstance(widget, RepositoryView):
                widget._history_view.invalidate_jobs()
        event.accept()
