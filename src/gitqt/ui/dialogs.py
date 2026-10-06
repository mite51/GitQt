"""Dialog windows for clone, branch create/delete, and dangerous confirmations."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gitqt.app.bootstrap import AppServices
from gitqt.services.session_jobs import SessionJobs
from gitqt.ui.theme import ERROR, ui_font


class CloneDialog(QDialog):
    """Clone a repository from a URL to a local destination."""

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._cloned_path: str | None = None
        self._jobs = SessionJobs(services.worker_pool, parent=self)
        self._jobs.progress.connect(lambda message: self._transfer_message.setText(message))
        self._jobs.busy_changed.connect(self._set_busy)

        self.setWindowTitle("Clone Repository")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("git@github.com:org/repo.git or https://...")
        self._url_input.textChanged.connect(self._on_url_changed)
        form.addRow("URL:", self._url_input)

        dest_row = QHBoxLayout()
        self._dest_input = QLineEdit(str(Path.home() / "Dev"))
        dest_row.addWidget(self._dest_input)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_destination)
        dest_row.addWidget(browse_btn)
        form.addRow("Destination:", dest_row)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("(auto-detected from URL)")
        form.addRow("Name:", self._name_input)

        self._branch_input = QLineEdit()
        self._branch_input.setPlaceholderText("default")
        form.addRow("Branch:", self._branch_input)

        self._submodules_check = QCheckBox("Recurse submodules")
        form.addRow("", self._submodules_check)

        layout.addLayout(form)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: {ERROR.name()}; font-size: 9pt;")
        self._error_label.setVisible(False)
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._transfer_message = QLabel()
        self._transfer_message.setWordWrap(True)
        layout.addWidget(self._transfer_message)

        buttons = QDialogButtonBox()
        self._clone_btn = buttons.addButton(
            "Clone Repository", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._clone_btn.setProperty("accent", True)
        self._clone_btn.setEnabled(False)
        self._cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._do_clone)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_path(self) -> str | None:
        return self._cloned_path

    def _on_url_changed(self, text: str) -> None:
        url = text.strip()
        self._clone_btn.setEnabled(bool(url))
        if not self._name_input.text().strip():
            inferred = _infer_repo_name(url)
            self._name_input.setPlaceholderText(inferred or "(auto-detected from URL)")

    def _browse_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Destination", self._dest_input.text())
        if path:
            self._dest_input.setText(path)

    def _set_busy(self, busy):
        for field in (self._url_input, self._dest_input, self._name_input, self._branch_input):
            field.setEnabled(not busy)
        self._clone_btn.setEnabled(not busy and bool(self._url_input.text().strip()))

    def _do_clone(self) -> None:
        url = self._url_input.text().strip()
        dest = self._dest_input.text().strip()
        name = self._name_input.text().strip() or _infer_repo_name(url) or "repo"
        branch = self._branch_input.text().strip()

        if not url:
            self._show_error("Repository URL is required.")
            return
        if not dest:
            self._show_error("Destination directory is required.")
            return

        target = Path(dest) / name
        if target.exists():
            self._show_error(f"Destination already exists: {target}")
            return

        runner = self._services.git_runner
        if runner is None:
            self._show_error("Git executable is not available.")
            return

        from gitqt.services.remote import RemoteService

        try:
            RemoteService.validate_url(url)
        except ValueError as error:
            self._show_error(str(error))
            return
        args = ["clone", "--progress"]
        if branch:
            args.extend(["--branch", branch])
        if self._submodules_check.isChecked():
            args.append("--recurse-submodules")
        args.extend(["--", url, str(target)])

        self._transfer_message.clear()
        self._progress.setVisible(True)
        self._clone_btn.setEnabled(False)
        self._error_label.setVisible(False)

        def apply(_):
            self._cloned_path = str(target)
            self.accept()

        def finished():
            self._progress.setVisible(False)
            self._clone_btn.setEnabled(True)

        self._jobs.write(
            lambda: runner.run(args, timeout_seconds=600),
            apply,
            lambda value: self._show_error(str(value)),
            finished,
            cancellable=True,
        )

    def reject(self) -> None:
        if self._jobs.busy:
            self._jobs.cancel()
            self._show_error("Stopping clone… The destination may contain a partial clone.")
            return
        super().reject()

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.setVisible(True)


class CreateBranchDialog(QDialog):
    """Create a new branch, optionally checking it out."""

    def __init__(
        self,
        current_branch: str | None = None,
        local_branches: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Branch")
        self.setMinimumWidth(420)

        self._branch_name: str = ""
        self._start_point: str = ""
        self._checkout: bool = True

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("feature/my-branch")
        self._name_input.textChanged.connect(self._validate)
        form.addRow("Branch name:", self._name_input)

        self._start_combo = QComboBox()
        self._start_combo.setEditable(True)
        if local_branches:
            self._start_combo.addItems(local_branches)
        if current_branch:
            idx = self._start_combo.findText(current_branch)
            if idx >= 0:
                self._start_combo.setCurrentIndex(idx)
        form.addRow("Start point:", self._start_combo)

        self._checkout_check = QCheckBox("Checkout immediately")
        self._checkout_check.setChecked(True)
        form.addRow("", self._checkout_check)

        layout.addLayout(form)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: {ERROR.name()}; font-size: 9pt;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        buttons = QDialogButtonBox()
        self._create_btn = buttons.addButton(
            "Create Branch", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._create_btn.setProperty("accent", True)
        self._create_btn.setEnabled(False)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def branch_name(self) -> str:
        return self._branch_name

    def start_point(self) -> str:
        return self._start_point

    def checkout(self) -> bool:
        return self._checkout

    def _validate(self) -> None:
        name = self._name_input.text().strip()
        self._create_btn.setEnabled(bool(name))
        if " " in name:
            self._error_label.setText("Branch names cannot contain spaces.")
            self._error_label.setVisible(True)
            self._create_btn.setEnabled(False)
        else:
            self._error_label.setVisible(False)

    def _accept(self) -> None:
        name = self._name_input.text().strip()
        if not name:
            return
        self._branch_name = name
        self._start_point = self._start_combo.currentText().strip()
        self._checkout = self._checkout_check.isChecked()
        self.accept()


class ConfirmationDialog(QDialog):
    """Generic confirmation dialog for dangerous Git operations."""

    def __init__(
        self,
        title: str,
        body: str,
        confirm_label: str = "Confirm",
        recovery_hint: str = "",
        danger: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        body_label = QLabel(body)
        body_label.setWordWrap(True)
        body_label.setFont(ui_font(11))
        layout.addWidget(body_label)

        if recovery_hint:
            hint_label = QLabel(recovery_hint)
            hint_label.setWordWrap(True)
            hint_label.setProperty("dim", True)
            hint_label.setFont(ui_font(9))
            layout.addWidget(hint_label)

        buttons = QDialogButtonBox()
        confirm_btn = buttons.addButton(confirm_label, QDialogButtonBox.ButtonRole.AcceptRole)
        if danger:
            confirm_btn.setProperty("danger", True)
        else:
            confirm_btn.setProperty("accent", True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CreateTagDialog(QDialog):
    """Create a lightweight or annotated tag."""

    def __init__(
        self,
        current_branch: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Tag")
        self.setMinimumWidth(420)

        self._tag_name: str = ""
        self._target: str = ""
        self._annotation: str = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("v1.0.0")
        self._name_input.textChanged.connect(self._validate)
        form.addRow("Tag name:", self._name_input)

        self._target_input = QLineEdit()
        self._target_input.setPlaceholderText(current_branch or "HEAD")
        form.addRow("Target:", self._target_input)

        self._annotation_input = QLineEdit()
        self._annotation_input.setPlaceholderText("(optional, makes annotated tag)")
        form.addRow("Annotation:", self._annotation_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox()
        self._create_btn = buttons.addButton("Create Tag", QDialogButtonBox.ButtonRole.AcceptRole)
        self._create_btn.setProperty("accent", True)
        self._create_btn.setEnabled(False)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def tag_name(self) -> str:
        return self._tag_name

    def target(self) -> str:
        return self._target

    def annotation(self) -> str:
        return self._annotation

    def _validate(self) -> None:
        self._create_btn.setEnabled(bool(self._name_input.text().strip()))

    def _accept(self) -> None:
        self._tag_name = self._name_input.text().strip()
        self._target = self._target_input.text().strip()
        self._annotation = self._annotation_input.text().strip()
        if self._tag_name:
            self.accept()


class CreateStashDialog(QDialog):
    """Create a stash with optional message and untracked inclusion."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Stash")
        self.setMinimumWidth(400)

        self._message: str = ""
        self._include_untracked: bool = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        self._message_input = QLineEdit()
        self._message_input.setPlaceholderText("WIP: description (optional)")
        form.addRow("Message:", self._message_input)

        self._untracked_check = QCheckBox("Include untracked files")
        form.addRow("", self._untracked_check)

        layout.addLayout(form)

        buttons = QDialogButtonBox()
        create_btn = buttons.addButton("Create Stash", QDialogButtonBox.ButtonRole.AcceptRole)
        create_btn.setProperty("accent", True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def stash_message(self) -> str:
        return self._message

    def include_untracked(self) -> bool:
        return self._include_untracked

    def _accept(self) -> None:
        self._message = self._message_input.text().strip()
        self._include_untracked = self._untracked_check.isChecked()
        self.accept()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_repo_name(url: str) -> str:
    """Extract repository name from a Git URL."""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url.rsplit("/", maxsplit=1)[-1].rsplit(":", maxsplit=1)[-1] or ""
