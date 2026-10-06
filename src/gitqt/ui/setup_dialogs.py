"""Identity and connection setup, available before opening a repository."""

import os
import shutil

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
)

from gitqt.services.git import GitCommandFailure, GitExecutable, GitRunner
from gitqt.services.remote import RemoteService
from gitqt.services.session_jobs import SessionJobs


class IdentityDialog(QDialog):
    def __init__(self, services, repository=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Commit Identity")
        self.jobs = SessionJobs(services.worker_pool, self)
        self.jobs.activate(repository)
        self.runner = services.git_runner
        self.repository = repository
        layout = QFormLayout(self)
        self.scope = QComboBox()
        self.scope.addItems(
            ["All repositories (global)"] + (["This repository"] if repository else [])
        )
        if repository:
            self.scope.setCurrentIndex(1)
        self.name = QLineEdit()
        self.email = QLineEdit()
        layout.addRow("Save to", self.scope)
        layout.addRow("Name", self.name)
        layout.addRow("Email", self.email)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addRow(self.message)
        self.save = QPushButton("Save Identity")
        layout.addRow(self.save)
        self.save.clicked.connect(self.write_identity)
        self.jobs.busy_changed.connect(lambda busy: self.save.setEnabled(not busy))
        self.scope.currentIndexChanged.connect(self.read_identity)
        self.read_identity()

    def read_identity(self):
        scope = "--local" if self.scope.currentIndex() == 1 else "--global"

        def run():
            result = []
            for key in ("user.name", "user.email"):
                try:
                    result.append(
                        self.runner.run(
                            ["config", scope, "--get", key], cwd=self.repository
                        ).stdout.strip()
                    )
                except GitCommandFailure as error:
                    if error.result.exit_code != 1:
                        raise
                    result.append("")
            return result

        self.jobs.read(
            "identity",
            run,
            lambda values: (self.name.setText(values[0]), self.email.setText(values[1])),
            lambda error: self.message.setText(str(error)),
        )

    def write_identity(self):
        name, email = self.name.text().strip(), self.email.text().strip()
        if not name or not email or "\n" in name + email:
            self.message.setText("Enter a name and email address.")
            return
        scope = "--local" if self.scope.currentIndex() == 1 else "--global"

        def run():
            self.runner.run(["config", scope, "user.name", name], cwd=self.repository)
            self.runner.run(["config", scope, "user.email", email], cwd=self.repository)

        self.jobs.write(
            run, lambda _: self.accept(), lambda error: self.message.setText(str(error))
        )

    def reject(self):
        if not self.jobs.busy:
            self.jobs.invalidate_reads()
            super().reject()


class ConnectionDialog(QDialog):
    def __init__(self, services, repository=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connection Setup")
        self.setMinimumWidth(660)
        self.jobs = SessionJobs(services.worker_pool, self)
        self.jobs.activate(repository)
        self.runner = services.git_runner
        self.repository = repository
        self.services = services
        layout = QFormLayout(self)
        self.url = QLineEdit()
        self.url.setPlaceholderText("Remote name (in a repository) or HTTPS / SSH URL")
        layout.addRow("Test connection", self.url)
        self.message = QPlainTextEdit("Checking Git, credential helpers, and SSH agent…")
        self.message.setReadOnly(True)
        self.message.setMinimumHeight(290)
        layout.addRow(self.message)
        self.test = QPushButton("Test / Retry")
        self.test.clicked.connect(self.test_connection)
        layout.addRow(self.test)
        self.key = QPushButton("Load SSH Key into Agent…")
        self.key.clicked.connect(self.load_key)
        layout.addRow(self.key)
        self.cancel = QPushButton("Cancel Operation")
        self.cancel.clicked.connect(self.jobs.cancel)
        layout.addRow(self.cancel)
        self.jobs.busy_changed.connect(self.busy)
        self.jobs.progress.connect(self.message.setPlainText)
        self.jobs.read(
            "diagnostics",
            self.diagnostics,
            self.message.setPlainText,
            lambda error: self.message.setPlainText(str(error)),
        )

    def busy(self, busy):
        self.test.setEnabled(not busy)
        self.key.setEnabled(not busy)
        self.url.setEnabled(not busy)
        self.cancel.setEnabled(busy)

    def ssh_runner(self):
        executable = shutil.which("ssh-add")
        if not executable:
            raise ValueError(
                "OpenSSH ssh-add is unavailable. Install OpenSSH and start an SSH agent, then"
                " restart GitQt so it inherits the agent environment."
            )
        runner = GitRunner(GitExecutable(executable))
        runner.credential_environment = self.runner.credential_environment
        return runner

    def diagnostics(self):
        version = self.runner.run(["--version"]).stdout.strip()
        try:
            helpers = (
                self.runner.run(["config", "--get-all", "credential.helper"], cwd=self.repository)
                .stdout.strip()
                .splitlines()
            )
        except GitCommandFailure as error:
            if error.result.exit_code != 1:
                raise
            helpers = []
        known = {"manager", "manager-core", "osxkeychain", "libsecret", "wincred", "cache", "store"}
        labels = [value if value in known else "custom helper" for value in helpers]
        try:
            keys = self.ssh_runner().run(["-l"], timeout_seconds=10).stdout
            agent = f"SSH agent available; {len(keys.splitlines())} key(s) loaded."
        except (GitCommandFailure, ValueError):
            agent = (
                "No usable SSH-agent keys detected. Start your system SSH agent, then load a key"
                " here. GitQt must inherit its environment; restart after agent setup."
            )
        return (
            f"{version}\nCredential helpers:"
            f" {', '.join(labels) or 'none configured'}.\n{agent}\n\nHTTPS: use a token when your"
            " host requires it. GitQt prompts when Git needs credentials; your configured helper"
            " controls storage. Configure a system credential manager for persistent secure"
            " storage.\nExpired access: update or remove the saved entry in that manager, then"
            " Test / Retry.\nSSH: load an existing key; host verification and passphrase prompts"
            " appear in GitQt. GitQt does not create keys or change trust automatically."
        )

    def test_connection(self):
        url = self.url.text().strip()
        try:
            RemoteService.validate_url(url)
        except ValueError as error:
            self.message.setPlainText(str(error))
            return
        self.jobs.write(
            lambda: self.runner.run(["ls-remote", "--", url], cwd=self.repository),
            lambda _: self.message.setPlainText(
                "Connection succeeded. Fetch or push can now be retried."
            ),
            lambda error: self.message.setPlainText(str(error)),
            cancellable=True,
        )

    def load_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose SSH Private Key", os.path.expanduser("~/.ssh")
        )
        if not path:
            return
        self.jobs.write(
            lambda: self.ssh_runner().run([path], timeout_seconds=600),
            lambda _: self.message.setPlainText(
                "Key loaded into the SSH agent. Test the connection next."
            ),
            lambda error: self.message.setPlainText(str(error)),
            cancellable=True,
        )

    def reject(self):
        if self.jobs.busy:
            self.jobs.cancel()
            self.message.setPlainText("Stopping operation… Close again after it finishes.")
            return
        self.jobs.invalidate_reads()
        super().reject()
