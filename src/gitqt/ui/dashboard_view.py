"""Dashboard screen for managing and opening repositories."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gitqt.app.bootstrap import AppServices
from gitqt.app.dashboard import DashboardViewModel
from gitqt.services.repositories import RepositoryEntry
from gitqt.services.session_jobs import SessionJobs
from gitqt.ui.theme import (
    ACCENT,
    ERROR,
    FG_DIM,
    SUCCESS,
    ui_font,
)


class DashboardView(QWidget):
    """Full-page dashboard for repository selection and management."""

    repository_opened = Signal(str)
    ready = Signal()

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._dashboard = DashboardViewModel(services.repositories, autoload=False)
        self._jobs = SessionJobs(services.worker_pool, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 24)
        layout.setSpacing(16)

        # Title area
        title_row = QHBoxLayout()
        title = QLabel("GitQt")
        title.setFont(ui_font(22, bold=True))
        title.setProperty("heading", True)
        title_row.addWidget(title)
        title_row.addStretch()

        git_info = QLabel()
        git_info.setProperty("dim", True)
        git_path = services.git.path if services.git else "Git not found"
        git_info.setText(f"Git: {git_path}")
        title_row.addWidget(git_info)
        layout.addLayout(title_row)

        # Subtitle
        subtitle = QLabel("Repositories")
        subtitle.setFont(ui_font(14, bold=True))
        layout.addWidget(subtitle)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._open_btn = QPushButton("  Open Repository...")
        self._open_btn.setProperty("accent", True)
        self._open_btn.setToolTip("Open a Git repository from disk (Ctrl+O)")
        self._open_btn.clicked.connect(self._open_repository_dialog)
        toolbar.addWidget(self._open_btn)

        self._add_btn = QPushButton("  Add Existing...")
        self._add_btn.setToolTip("Track an existing repository without opening it")
        self._add_btn.clicked.connect(self._add_repository_dialog)
        toolbar.addWidget(self._add_btn)

        self._clone_btn = QPushButton("  Clone...")
        self._clone_btn.setToolTip("Clone a repository from a remote URL")
        self._clone_btn.clicked.connect(self._clone_dialog)
        toolbar.addWidget(self._clone_btn)
        self._init_btn = QPushButton("New Repository…")
        self._init_btn.clicked.connect(self._initialize_dialog)
        toolbar.addWidget(self._init_btn)

        toolbar.addStretch()

        self._remove_btn = QPushButton("  Remove Selected")
        self._remove_btn.setProperty("danger", True)
        self._remove_btn.setToolTip("Remove the selected repository from the list")
        self._remove_btn.clicked.connect(self._remove_selected)
        toolbar.addWidget(self._remove_btn)

        layout.addLayout(toolbar)

        # Status message
        self._status_label = QLabel()
        self._status_label.setProperty("subheading", True)
        layout.addWidget(self._status_label)

        # Repository list
        self._repo_list = QListWidget()
        self._repo_list.setSpacing(2)
        self._repo_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._repo_list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._repo_list, stretch=1)

        # Empty state
        self._empty_widget = QWidget()
        empty_layout = QVBoxLayout(self._empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_icon = QLabel("📂")
        empty_icon.setFont(ui_font(36))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)

        empty_title = QLabel("No repositories yet")
        empty_title.setFont(ui_font(16, bold=True))
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_hint = QLabel("Open a repository, add an existing one, or clone from a remote URL.")
        empty_hint.setProperty("dim", True)
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_hint)

        layout.addWidget(self._empty_widget, stretch=1)

        self._jobs.busy_changed.connect(lambda busy: self.setEnabled(not busy))
        self._refresh()

    @property
    def view_model(self) -> DashboardViewModel:
        return self._dashboard

    def refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        def run():
            model = DashboardViewModel(self._services.repositories)
            return model.state

        def apply(state):
            self._dashboard.state = state
            self._display_state(state)
            self.ready.emit()

        self._jobs.read(
            "dashboard", run, apply, lambda error: self._status_label.setText(str(error))
        )

    def _display_state(self, state):
        has_repos = bool(state.repositories)

        self._status_label.setText(
            (self._services.settings_store.warning or "") + " " + state.message
        )
        self._repo_list.setVisible(has_repos)
        self._empty_widget.setVisible(not has_repos)
        self._remove_btn.setEnabled(has_repos)

        self._repo_list.clear()
        for repo in state.repositories:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, repo.path)
            item.setSizeHint(item.sizeHint().expandedTo(self._repo_list.sizeHint()))
            widget = _RepositoryListItem(repo)
            item.setSizeHint(widget.sizeHint())
            self._repo_list.addItem(item)
            self._repo_list.setItemWidget(item, widget)

    def _open_path(self, path, *, add_only=False):
        service = self._services.repositories

        def apply(result):
            if result.is_valid:
                if not add_only:
                    self.repository_opened.emit(result.path)
            else:
                QMessageBox.warning(self, "Repository Not Opened", result.message)

        self._jobs.write(
            lambda: service.add_repository(path) if add_only else service.open_repository(path),
            apply,
            lambda error: QMessageBox.warning(self, "Repository Not Opened", str(error)),
            self._refresh,
        )

    def _open_repository_dialog(self):
        directory = self._pick_directory()
        if directory:
            self._open_path(directory)

    def _add_repository_dialog(self):
        directory = self._pick_directory()
        if directory:
            self._open_path(directory, add_only=True)

    def _clone_dialog(self):
        from gitqt.ui.dialogs import CloneDialog

        dialog = CloneDialog(self._services, parent=self)
        if dialog.exec() and dialog.result_path():
            self._open_path(dialog.result_path())

    def _initialize_dialog(self):
        from gitqt.ui.init_dialog import InitDialog

        dialog = InitDialog(self._services, self)
        if dialog.exec() and dialog.path:
            self._open_path(dialog.path)

    def _remove_selected(self):
        item = self._repo_list.currentItem()
        if item is not None:
            path = item.data(Qt.ItemDataRole.UserRole)
            self._jobs.write(
                lambda: self._services.repositories.remove_repository(path),
                lambda _: None,
                lambda error: self._status_label.setText(str(error)),
                self._refresh,
            )

    def _on_item_double_clicked(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._open_path(path)

    def _on_selection_changed(self) -> None:
        self._remove_btn.setEnabled(self._repo_list.currentItem() is not None)

    def _pick_directory(self) -> str:
        start = str(Path.home())
        if self._dashboard.state.repositories:
            start = self._dashboard.state.repositories[0].path
        return QFileDialog.getExistingDirectory(self, "Select Repository Directory", start)


class _RepositoryListItem(QWidget):
    """Custom widget rendered for each repository row in the dashboard list."""

    def __init__(self, entry: RepositoryEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # Status indicator dot
        dot = QLabel()
        if not entry.validation.is_valid:
            dot.setText("●")
            dot.setStyleSheet(f"color: {ERROR.name()}; font-size: 10pt;")
            dot.setToolTip(f"Invalid: {entry.validation.message}")
        elif entry.is_last_opened:
            dot.setText("●")
            dot.setStyleSheet(f"color: {SUCCESS.name()}; font-size: 10pt;")
            dot.setToolTip("Last opened repository")
        else:
            dot.setText("●")
            dot.setStyleSheet(f"color: {FG_DIM.name()}; font-size: 10pt;")
        layout.addWidget(dot)

        # Name + path
        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        name_label = QLabel(entry.name)
        name_label.setFont(ui_font(11, bold=True))
        if not entry.validation.is_valid:
            name_label.setStyleSheet(f"color: {FG_DIM.name()};")
        text_col.addWidget(name_label)

        path_label = QLabel(entry.path)
        path_label.setProperty("dim", True)
        path_label.setFont(ui_font(9))
        text_col.addWidget(path_label)

        layout.addLayout(text_col, stretch=1)

        # Badges
        if entry.is_last_opened:
            badge = QLabel("last opened")
            badge.setStyleSheet(
                f"color: {ACCENT.name()}; background: rgba(137, 180, 250, 25); "
                "border-radius: 4px; padding: 2px 8px; font-size: 9pt;"
            )
            layout.addWidget(badge)

        if not entry.validation.is_valid:
            badge = QLabel("invalid")
            badge.setStyleSheet(
                f"color: {ERROR.name()}; background: rgba(243, 139, 168, 25); "
                "border-radius: 4px; padding: 2px 8px; font-size: 9pt;"
            )
            layout.addWidget(badge)

        self.setMinimumHeight(48)
