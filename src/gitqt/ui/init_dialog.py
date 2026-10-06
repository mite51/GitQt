from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from gitqt.services.repositories import RepositoryValidator
from gitqt.services.session_jobs import SessionJobs


class InitDialog(QDialog):
    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Repository")
        self.setMinimumWidth(540)
        self.path = None
        self.jobs = SessionJobs(services.worker_pool, self)
        self.validator = RepositoryValidator(services.git_runner)
        form = QFormLayout(self)
        self.destination = QLineEdit()
        form.addRow("New or empty directory", self.destination)
        browse = QPushButton("Choose directory…")
        browse.clicked.connect(
            lambda: self.destination.setText(
                QFileDialog.getExistingDirectory(self, "Choose empty directory", str(Path.home()))
                or self.destination.text()
            )
        )
        form.addRow(browse)
        self.branch = QLineEdit("main")
        form.addRow("Initial branch", self.branch)
        self.readme = QCheckBox("Create README.md")
        self.ignore = QCheckBox("Create .gitignore")
        form.addRow(self.readme)
        form.addRow(self.ignore)
        self.message = QLabel(
            "Starter files remain uncommitted so you can review your first commit."
        )
        self.message.setWordWrap(True)
        form.addRow(self.message)
        self.create = QPushButton("Create Repository")
        self.create.clicked.connect(self.initialize)
        form.addRow(self.create)
        self.jobs.busy_changed.connect(lambda busy: self.create.setEnabled(not busy))

    def initialize(self):
        path, branch = self.destination.text().strip(), self.branch.text().strip()
        if not path or not branch:
            self.message.setText("Enter a directory and initial branch name.")
            return
        readme, ignore = self.readme.isChecked(), self.ignore.isChecked()

        def apply(result):
            if result.is_valid:
                self.path = result.path
                self.accept()
            else:
                self.message.setText(result.message)

        self.jobs.write(
            lambda: self.validator.initialize(path, branch=branch, readme=readme, gitignore=ignore),
            apply,
            lambda error: self.message.setText(str(error)),
        )

    def reject(self):
        if not self.jobs.busy:
            super().reject()
