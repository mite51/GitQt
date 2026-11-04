"""Repository chooser shared by the empty window and repository-tab button."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gitqt.app.bootstrap import AppServices
from gitqt.services.session_jobs import SessionJobs
from gitqt.ui.theme import ui_font


class AddRepositoryDialog(QDialog):
    """Open, clone, initialize, or pick a recently used repository."""

    def __init__(self, services: AppServices, parent=None) -> None:
        super().__init__(parent)
        self._services = services
        self._jobs = SessionJobs(services.worker_pool, self)
        self._result_path: str | None = None
        self.setWindowTitle("Add Repository")
        self.setMinimumSize(620, 430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Add a repository tab")
        title.setFont(ui_font(16, bold=True))
        layout.addWidget(title)
        hint = QLabel(
            "Open a recent repository, choose one on disk, clone, or initialize a new one."
        )
        hint.setProperty("dim", True)
        layout.addWidget(hint)

        self._recent = QListWidget()
        self._recent.setAlternatingRowColors(True)
        self._recent.itemDoubleClicked.connect(
            lambda item: self._open_path(item.data(Qt.ItemDataRole.UserRole))
        )
        layout.addWidget(self._recent, stretch=1)

        self._status = QLabel("Loading recent repositories…")
        self._status.setProperty("dim", True)
        layout.addWidget(self._status)

        actions = QHBoxLayout()
        self._open_button = QPushButton("Open Existing…")
        self._open_button.setProperty("accent", True)
        self._open_button.clicked.connect(self._choose_existing)
        actions.addWidget(self._open_button)
        self._clone_button = QPushButton("Clone…")
        self._clone_button.clicked.connect(self._clone)
        actions.addWidget(self._clone_button)
        self._init_button = QPushButton("Initialize New…")
        self._init_button.clicked.connect(self._initialize)
        actions.addWidget(self._init_button)
        actions.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        layout.addLayout(actions)

        self._jobs.busy_changed.connect(self._set_busy)
        self._load_recents()

    def result_path(self) -> str | None:
        return self._result_path

    def _set_busy(self, busy: bool) -> None:
        for widget in (self._recent, self._open_button, self._clone_button, self._init_button):
            widget.setEnabled(not busy)

    def _load_recents(self) -> None:
        def apply(repositories) -> None:
            self._recent.clear()
            for repository in repositories:
                item = QListWidgetItem(repository.name)
                item.setData(Qt.ItemDataRole.UserRole, repository.path)
                item.setToolTip(repository.path)
                if not repository.validation.is_valid:
                    item.setText(f"⚠  {repository.name}")
                    item.setToolTip(f"{repository.path}\n{repository.validation.message}")
                self._recent.addItem(item)
            self._status.setText(
                "Double-click a recent repository to open it."
                if repositories
                else "No recent repositories."
            )

        self._jobs.read(
            "recent_repositories",
            self._services.repositories.list_recent,
            apply,
            lambda error: self._status.setText(str(error)),
        )

    def _choose_existing(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select Repository Directory", str(Path.home())
        )
        if directory:
            self._open_path(directory)

    def _open_path(self, path: str) -> None:
        def apply(validation) -> None:
            if not validation.is_valid:
                QMessageBox.warning(self, "Repository Not Opened", validation.message)
                return
            self._result_path = validation.path
            self.accept()

        self._status.setText("Opening repository…")
        self._jobs.write(
            lambda: self._services.repositories.open_repository(path),
            apply,
            lambda error: QMessageBox.warning(self, "Repository Not Opened", str(error)),
        )

    def _clone(self) -> None:
        from gitqt.ui.dialogs import CloneDialog

        dialog = CloneDialog(self._services, parent=self)
        if dialog.exec() and dialog.result_path():
            self._open_path(dialog.result_path())

    def _initialize(self) -> None:
        from gitqt.ui.init_dialog import InitDialog

        dialog = InitDialog(self._services, self)
        if dialog.exec() and dialog.path:
            self._open_path(dialog.path)
