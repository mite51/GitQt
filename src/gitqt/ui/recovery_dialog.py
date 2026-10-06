from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gitqt.services.recovery import RecoveryService
from gitqt.services.session_jobs import SessionJobs


class RecoveryDialog(QDialog):
    def __init__(self, services, repository, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recovery")
        self.resize(760, 440)
        self.jobs = SessionJobs(services.worker_pool, self)
        self.jobs.activate(repository)
        self.service = RecoveryService(services.git_runner)
        self.repository = repository
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Committed history creates a new branch. Saved work restores into a clean working"
            " tree.\nFile recovery backs up newer contents first and leaves the index"
            " unchanged.\nGitQt recovery references are retained until manually removed; reflog"
            " entries expire under Git's policy."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.entries = QListWidget()
        layout.addWidget(self.entries)
        self.message = QLabel("Loading…")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.restore = QPushButton("Recover Selected…")
        self.restore.clicked.connect(self.recover)
        layout.addWidget(self.restore)
        self.jobs.busy_changed.connect(lambda busy: self.restore.setEnabled(not busy))
        self.reload()

    def reload(self):
        def apply(entries):
            self.entries.clear()
            for entry in entries:
                self.entries.addItem(f"{entry.kind}: {entry.description}")
                self.entries.item(self.entries.count() - 1).setData(Qt.ItemDataRole.UserRole, entry)
            self.message.setText("Choose an entry." if entries else "No recovery entries yet.")

        self.jobs.read(
            "recovery",
            lambda: self.service.entries(self.repository),
            apply,
            lambda error: self.message.setText(str(error)),
        )

    def recover(self):
        item = self.entries.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        branch = None
        if entry.kind in {"commit", "tag"}:
            branch, ok = QInputDialog.getText(
                self,
                "Recovered Reference",
                "New tag name:" if entry.kind == "tag" else "New branch name:",
            )
            if not ok:
                return
        elif (
            QMessageBox.question(
                self, "Recover Saved Work", f"Restore {entry.description}?\n{self.repository}"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.jobs.write(
            lambda: self.service.recover(self.repository, entry, branch_name=branch),
            self.message.setText,
            lambda error: self.message.setText(str(error)),
        )

    def reject(self):
        if not self.jobs.busy:
            self.jobs.invalidate_reads()
            super().reject()
