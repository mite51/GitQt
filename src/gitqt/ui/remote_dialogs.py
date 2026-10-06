"""Explicit destinations and remote configuration for desktop workflows."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gitqt.services.process_context import redact
from gitqt.services.session_jobs import SessionJobs


class TransferDialog(QDialog):
    def __init__(self, operation, remotes, tracking, selected=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(operation.title())
        self.setMinimumWidth(560)
        self.operation = operation
        self.remotes = remotes
        layout = QFormLayout(self)
        self.remote = QComboBox()
        self.remote.addItems([remote.name for remote in remotes])
        upstream_remote, _, upstream_branch = (
            tracking.upstream if tracking and tracking.upstream else ""
        ).partition("/")
        self.remote.setCurrentText(selected or upstream_remote or "origin")
        layout.addRow("Remote", self.remote)
        self.destination = QLabel()
        self.destination.setWordWrap(True)
        layout.addRow("Destination", self.destination)
        self.branch = QLineEdit(upstream_branch or (tracking.branch_name if tracking else "") or "")
        layout.addRow("Remote branch", self.branch)
        layout.setRowVisible(self.branch, operation != "fetch")
        self.option = QCheckBox(
            "Remove stale remote-tracking branches"
            if operation == "fetch"
            else "Publish and set upstream"
        )
        self.option.setChecked(operation == "push" and bool(tracking and not tracking.upstream))
        layout.addRow(self.option)
        self.option.setVisible(operation != "pull")
        self.strategy = QComboBox()
        self.strategy.addItems(["Fast-forward only", "Merge", "Rebase"])
        self.strategy.setVisible(operation == "pull")
        layout.addRow(self.strategy)
        self.force = QCheckBox("Force with lease — preview remote commits before confirming")
        self.force.setVisible(operation == "push")
        layout.addRow(self.force)
        self.force.toggled.connect(self.force_changed)
        self.hint = QLabel(
            (
                "Push sends the current HEAD to the destination branch shown above.\n"
                if operation == "push"
                else ""
            )
            + "Cancellation stops Git; it cannot undo work already completed."
        )
        self.hint.setWordWrap(True)
        layout.addRow(self.hint)
        self.go = QPushButton(operation.title() + "…")
        self.go.setEnabled(
            bool(remotes)
            and (operation != "push" or bool(tracking and not tracking.is_detached_head))
        )
        self.go.clicked.connect(self.accept)
        layout.addRow(self.go)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        layout.addRow(cancel)
        self.remote.currentTextChanged.connect(self.update_destination)
        self.branch.textChanged.connect(self.update_destination)
        self.update_destination()

    def force_changed(self, checked):
        if checked:
            self.option.setChecked(False)
        self.option.setEnabled(not checked)
        self.option.setToolTip(
            "Force push publishes the exact previewed commit. Use a normal push to set upstream"
            " afterward."
            if checked
            else ""
        )

    def update_destination(self):
        remote = next(
            (item for item in self.remotes if item.name == self.remote.currentText()), None
        )
        url = (
            (remote.push_url if self.operation == "push" else remote.fetch_url)
            if remote
            else "Add a remote in Repository → Remotes first."
        )
        self.destination.setText(
            redact(url) + (f" → {self.branch.text()}" if self.operation != "fetch" else "")
        )


class RemotesDialog(QDialog):
    def __init__(self, services, repository, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remotes")
        self.setMinimumWidth(620)
        self.jobs = SessionJobs(services.worker_pool, self)
        self.jobs.activate(repository)
        self.service = services.remote
        self.repository = repository
        self.snapshot = []
        layout = QVBoxLayout(self)
        self.choice = QComboBox()
        self.choice.currentIndexChanged.connect(self.select)
        layout.addWidget(self.choice)
        form = QFormLayout()
        self.name = QLineEdit()
        self.fetch = QLineEdit()
        self.push = QLineEdit()
        form.addRow("Name", self.name)
        form.addRow("Fetch URL", self.fetch)
        form.addRow("Push URL (optional)", self.push)
        layout.addLayout(form)
        self.save = QPushButton("Save Remote")
        self.save.clicked.connect(self.save_remote)
        layout.addWidget(self.save)
        self.remove = QPushButton("Remove Remote…")
        self.remove.clicked.connect(self.remove_remote)
        layout.addWidget(self.remove)
        self.message = QLabel("Loading…")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.jobs.busy_changed.connect(self.set_busy)
        self.reload()

    def set_busy(self, busy):
        for widget in (self.choice, self.name, self.fetch, self.push, self.save, self.remove):
            widget.setEnabled(not busy)

    def select(self, index):
        remote = self.snapshot[index - 1] if 0 < index <= len(self.snapshot) else None
        self.name.setText(remote.name if remote else "")
        self.fetch.setText(remote.fetch_url if remote else "")
        self.push.setText(remote.push_url if remote else "")

    def reload(self):
        def apply(snapshot):
            self.snapshot = snapshot.remotes
            self.choice.clear()
            self.choice.addItems(["Add remote…"] + [remote.name for remote in self.snapshot])
            self.message.setText(
                "Inspect and edit remote URLs here. Embedded passwords or tokens are rejected; use"
                " the authentication prompt."
            )

        self.jobs.read(
            "remotes",
            lambda: self.service.list_remotes(self.repository, force_refresh=True),
            apply,
            lambda error: self.message.setText(str(error)),
        )

    def save_remote(self):
        old = self.choice.currentText() if self.choice.currentIndex() > 0 else None
        name, fetch, push = (
            self.name.text().strip(),
            self.fetch.text().strip(),
            self.push.text().strip(),
        )
        self.jobs.write(
            lambda: self.service.configure(
                self.repository, name, fetch, push or None, previous=old
            ),
            lambda _: self.reload(),
            lambda error: self.message.setText(str(error)),
        )

    def remove_remote(self):
        if self.choice.currentIndex() <= 0:
            return
        name = self.choice.currentText()
        if (
            QMessageBox.question(
                self,
                "Remove Remote",
                f"Remove {name} and its tracking configuration?\nRecovery retains its URLs and"
                " tracked commit tips.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.jobs.write(
            lambda: self.service.remove(self.repository, name),
            lambda _: self.reload(),
            lambda error: self.message.setText(str(error)),
        )

    def reject(self):
        if not self.jobs.busy:
            self.jobs.invalidate_reads()
            super().reject()
