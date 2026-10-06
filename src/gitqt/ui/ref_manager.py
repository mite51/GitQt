"""Branch and tag lifecycle controls, including guarded remote operations."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from gitqt.services.process_context import redact
from gitqt.services.ref_management import RefManagementService
from gitqt.services.session_jobs import SessionJobs


class RefManagerDialog(QDialog):
    def __init__(self, services, repository, parent=None, selection=None):
        super().__init__(parent)
        self.setWindowTitle("Branches and Tags")
        self.resize(740, 640)
        self.services, self.repository = services, repository
        self.service = RefManagementService(services.git_runner)
        self.jobs = SessionJobs(services.worker_pool, self)
        self.jobs.activate(repository)
        self.selection = selection
        self.snapshot = None
        layout = QVBoxLayout(self)
        self.kind = QComboBox()
        self.kind.addItems(["Local branches", "Remote branches", "Local tags", "Remote tags"])
        self.kind.currentIndexChanged.connect(self.populate)
        layout.addWidget(self.kind)
        self.refs = QListWidget()
        self.refs.setStyleSheet("QListWidget::item:selected { color: #ffffff; }")
        self.refs.currentTextChanged.connect(self.inspect)
        layout.addWidget(self.refs)
        form = QFormLayout()
        self.action = QComboBox()
        form.addRow("Action", self.action)
        self.value = QLineEdit()
        self.value.setPlaceholderText("New local branch name, or remote/branch for upstream")
        form.addRow("Name / upstream", self.value)
        self.remote = QComboBox()
        form.addRow("Remote destination", self.remote)
        layout.addLayout(form)
        self.form = form
        self.action.currentTextChanged.connect(self.update_fields)
        self.load_tags = QPushButton("Load Tags from Selected Remote")
        self.load_tags.clicked.connect(self.remote_tags)
        layout.addWidget(self.load_tags)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        layout.addWidget(self.details)
        self.run_button = QPushButton("Run Selected Action…")
        self.run_button.clicked.connect(self.execute)
        layout.addWidget(self.run_button)
        self.cancel = QPushButton("Cancel Transfer")
        self.cancel.clicked.connect(self.jobs.cancel)
        self.cancel.hide()
        layout.addWidget(self.cancel)
        self.jobs.busy_changed.connect(self.busy)
        self.jobs.progress.connect(self.details.setPlainText)
        self.remote.currentTextChanged.connect(self.clear_remote_tags)
        self.reload()

    def busy(self, value):
        for widget in (
            self.kind,
            self.refs,
            self.value,
            self.remote,
            self.action,
            self.run_button,
            self.load_tags,
        ):
            widget.setEnabled(not value)
        self.cancel.setVisible(value and self.jobs.cancellable)
        if not value:
            self.update_fields()

    def update_fields(self):
        action = self.action.currentText()
        self.form.setRowVisible(self.value, self.kind.currentIndex() < 2)
        self.form.setRowVisible(self.remote, self.kind.currentIndex() == 3 or action == "Push tag")
        self.value.setPlaceholderText(
            "Remote/branch, for example origin/main"
            if action == "Set upstream"
            else "New local branch name"
        )

    def reload(self):
        def run():
            path = self.repository
            return (
                self.services.branch.get_snapshot(path, force_refresh=True),
                self.services.tag.list_tags(path, force_refresh=True),
                self.services.remote.list_remotes(path, force_refresh=True),
            )

        def apply(snapshot):
            self.snapshot = snapshot
            selected_remote = self.remote.currentText()
            self.remote.clear()
            self.remote.addItems([item.name for item in snapshot[2].remotes])
            self.remote.setCurrentText(selected_remote)
            if self.selection:
                kind, name = self.selection
                self.kind.setCurrentIndex(
                    {"local_branch": 0, "remote_branch": 1, "tag": 2}.get(kind, 0)
                )
                self.populate()
                matches = self.refs.findItems(name, Qt.MatchFlag.MatchExactly)
                if matches:
                    self.refs.setCurrentItem(matches[0])
                self.selection = None
            else:
                self.populate()

        self.jobs.read("refs", run, apply, self.error)

    def populate(self):
        self.jobs.invalidate_channel("tag-inspection")
        current = self.refs.currentItem().text() if self.refs.currentItem() else ""
        self.refs.clear()
        index = self.kind.currentIndex()
        self.action.clear()
        choices = [
            ["Rename branch", "Set upstream"],
            ["Checkout as local tracking branch", "Delete remote branch"],
            ["Inspect tag", "Checkout tag (detached HEAD)", "Delete local tag", "Push tag"],
            ["Delete remote tag"],
        ]
        self.action.addItems(choices[index])
        self.load_tags.setVisible(index == 3)
        self.value.setEnabled(index < 2)
        if self.snapshot:
            rows = [
                self.snapshot[0].local_branches,
                self.snapshot[0].remote_branches,
                self.snapshot[1].tags,
                [],
            ][index]
            self.refs.addItems([row.name for row in rows if not row.name.endswith("/HEAD")])
            matches = self.refs.findItems(current, Qt.MatchFlag.MatchExactly)
            if matches:
                self.refs.setCurrentItem(matches[0])
        self.details.setPlainText(
            "Select a reference and action. Remote deletion retains recovery data and requires a"
            " preview."
        )

    def clear_remote_tags(self):
        if self.kind.currentIndex() == 3:
            self.refs.clear()

    def inspect(self, name):
        if name and self.kind.currentIndex() == 2:
            self.jobs.read(
                "tag-inspection",
                lambda: self.service.inspect_tag(self.repository, name),
                self.details.setPlainText,
                self.error,
            )

    def error(self, error):
        self.details.setPlainText(str(error))

    def remote_tags(self):
        remote = self.remote.currentText()
        if not remote:
            self.error("Add a remote first.")
            return

        def run():
            runner = self.services.git_runner
            url = runner.run(
                ["remote", "get-url", "--push", remote], cwd=self.repository
            ).stdout.strip()
            raw = runner.run(
                ["ls-remote", "--refs", "--tags", "--", url], cwd=self.repository
            ).stdout
            return [row.partition("\t")[2].removeprefix("refs/tags/") for row in raw.splitlines()]

        def apply(names):
            self.refs.clear()
            self.refs.addItems(names)
            self.details.setPlainText(f"{len(names)} tags at {remote}.")

        self.jobs.write(run, apply, self.error, cancellable=True)

    def execute(self):
        item = self.refs.currentItem()
        if item is None:
            return
        name, action, value = item.text(), self.action.currentText(), self.value.text().strip()
        repo, service = self.repository, self.service
        remote = self.remote.currentText()
        if action == "Inspect tag":
            self.inspect(name)
            return
        if action == "Rename branch":
            self.write(lambda: service.rename_branch(repo, name, value))
        elif action == "Set upstream":
            self.write(lambda: service.set_upstream(repo, name, value))
        elif action == "Checkout as local tracking branch":
            self.write(lambda: service.track_branch(repo, name, value or name.split("/", 1)[-1]))
        elif action in {"Checkout tag (detached HEAD)", "Delete local tag"}:
            text = f"{action}: {name}?\n" + (
                "The tag object and annotation will be retained in Recovery."
                if action == "Delete local tag"
                else "Create a branch before making new commits in detached HEAD state."
            )
            if QMessageBox.question(self, action, text) == QMessageBox.StandardButton.Yes:
                self.write(
                    lambda: (
                        service.delete_tag(repo, name)
                        if action == "Delete local tag"
                        else service.checkout_tag(repo, name)
                    )
                )
        else:
            kind = "tags"
            if action == "Delete remote branch":
                prefixes = [
                    entry.name
                    for entry in self.snapshot[2].remotes
                    if name.startswith(entry.name + "/")
                ]
                if not prefixes:
                    self.error("No configured remote matches this reference.")
                    return
                remote = max(prefixes, key=len)
                name = name[len(remote) + 1 :]
                kind = "heads"
            if not remote:
                self.error("Choose a remote destination.")
                return
            push = action == "Push tag"

            def apply(preview):
                text = (
                    f"{action}: {preview.ref}\nDestination: {redact(preview.url)}\nObject:"
                    f" {preview.oid}\n\n"
                    + (
                        "Existing different tags are never overwritten."
                        if push
                        else (
                            "The object is retained in Recovery. Deletion is rejected if the remote"
                            " changes after this preview."
                        )
                    )
                )
                if QMessageBox.question(self, action, text) == QMessageBox.StandardButton.Yes:
                    self.write(
                        lambda: (
                            service.push_tag(repo, preview)
                            if push
                            else service.delete_remote(repo, preview)
                        ),
                        cancellable=True,
                    )

            self.jobs.write(
                lambda: (
                    service.prepare_tag_push(repo, remote, name)
                    if push
                    else service.prepare_remote(repo, remote, kind, name)
                ),
                apply,
                self.error,
                cancellable=True,
            )

    def write(self, run, *, cancellable=False):
        def apply(_):
            self.reload()
            self.details.setPlainText(
                "Operation completed. Refresh remote tags to see the latest list."
            )

        self.jobs.write(run, apply, self.error, cancellable=cancellable)

    def reject(self):
        if self.jobs.busy:
            if self.jobs.cancellable:
                self.jobs.cancel()
            return
        self.jobs.invalidate_reads()
        super().reject()
