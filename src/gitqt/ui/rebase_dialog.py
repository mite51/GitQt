"""Branch rebase preview and interactive commit editor."""

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from gitqt.services.rebase import RebaseService, RebaseStep
from gitqt.services.session_jobs import SessionJobs


class RebaseDialog(QDialog):
    def __init__(
        self,
        runner,
        pool,
        repository,
        snapshot,
        *,
        source="",
        target="",
        interactive=False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Rebase")
        self.resize(780, 700)
        self.service = RebaseService(runner)
        self.repository = repository
        self.jobs = SessionJobs(pool, self)
        self.jobs.activate(repository)
        self.preview = None
        self._steps = {}
        self._updating = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.source = QComboBox()
        self.target = QComboBox()
        for branch in snapshot.local_branches:
            self.source.addItem(branch.name, branch.name)
            self.target.addItem(branch.name, f"refs/heads/{branch.name}")
        for branch in snapshot.remote_branches:
            if not branch.name.endswith("/HEAD"):
                self.target.addItem(branch.name + " (remote)", f"refs/remotes/{branch.name}")
        self.source.setCurrentIndex(self.source.findData(source or snapshot.tracking.branch_name))
        if target and self.target.findData(target) < 0:
            self.target.addItem(target, target)
        self.target.setCurrentIndex(self.target.findData(target))
        form.addRow("Rebase branch", self.source)
        form.addRow("Onto", self.target)
        layout.addLayout(form)
        self.interactive = QCheckBox("Interactive rebase — edit and reorder commits")
        self.interactive.setChecked(interactive)
        layout.addWidget(self.interactive)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        layout.addWidget(QLabel("Replay order: oldest first. Drag commits or use Move Up / Down."))
        self.commits = QListWidget()
        self.commits.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.commits.setStyleSheet("QListWidget::item:selected { color: #ffffff; }")
        layout.addWidget(self.commits, 1)
        controls = QHBoxLayout()
        self.up = QPushButton("Move Up")
        self.down = QPushButton("Move Down")
        self.action = QComboBox()
        for label, value in (
            ("Keep", "pick"),
            ("Edit message", "reword"),
            ("Squash into previous", "squash"),
            ("Fixup into previous", "fixup"),
            ("Drop", "drop"),
        ):
            self.action.addItem(label, value)
        controls.addWidget(self.up)
        controls.addWidget(self.down)
        controls.addWidget(self.action, 1)
        layout.addLayout(controls)
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText("Select Edit message to change the full commit message.")
        self.message.setMaximumHeight(160)
        layout.addWidget(self.message)
        help_text = QLabel(
            "Squash combines messages; fixup keeps the preceding message. Dropped commits "
            "are removed. Rebase rewrites history; coordinate before rewriting shared commits.\n"
            "The source branch will be checked out. Its original tip is retained in Recovery. "
            "If Git pauses, use Continue, Skip Commit, or Abort in Working Tree."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start = self.buttons.addButton("Rebase", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.source.currentIndexChanged.connect(self.reload)
        self.target.currentIndexChanged.connect(self.reload)
        self.interactive.toggled.connect(self._validate)
        self.commits.currentItemChanged.connect(self._select)
        self.commits.model().rowsMoved.connect(self._validate)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.action.currentIndexChanged.connect(self._edit)
        self.message.textChanged.connect(self._edit)
        self.finished.connect(lambda: self.jobs.invalidate_reads())
        self.reload()

    def reload(self):
        self.preview = None
        self._steps = {}
        self.commits.clear()
        self.start.setEnabled(False)
        self.error.clear()
        self.jobs.invalidate_channel("preview")
        source, target = self.source.currentData(), self.target.currentData()
        if not source or not target:
            self.summary.setText("Choose a source branch and a destination.")
            self._validate()
            return
        self.summary.setText("Loading rebase preview…")
        self._validate()
        self.jobs.read(
            "preview",
            lambda: self.service.preview(self.repository, source, target),
            self._loaded,
            self._failed,
        )

    def _failed(self, error):
        self.summary.setText("Rebase is unavailable.")
        self.error.setText(str(error))

    def _loaded(self, preview):
        self.preview = preview
        self.summary.setText(
            f"{preview.source} → {self.target.currentText()}\n"
            f"{len(preview.commits)} commit(s) to replay. Destination: {preview.target_oid[:12]}."
            + (
                " Merge commits will be flattened by a regular rebase."
                if preview.has_merges
                else ""
            )
        )
        for commit in preview.commits:
            self._steps[commit.oid] = RebaseStep(commit.oid, message=commit.message)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, commit.oid)
            self._label(item)
            self.commits.addItem(item)
        if self.commits.count():
            self.commits.setCurrentRow(0)
        self._validate()

    def _label(self, item):
        step = self._steps[item.data(Qt.ItemDataRole.UserRole)]
        subject = step.message.splitlines()[0] if step.message else "(empty message)"
        item.setText(f"{step.action:7}  {step.oid[:8]}  {subject}")

    def _select(self, *_):
        self._updating = True
        item = self.commits.currentItem()
        if item is not None:
            step = self._steps[item.data(Qt.ItemDataRole.UserRole)]
            self.action.setCurrentIndex(self.action.findData(step.action))
            self.message.setPlainText(step.message)
        else:
            self.message.clear()
        self._updating = False
        self._validate()

    def _edit(self, *_):
        item = self.commits.currentItem()
        if self._updating or item is None:
            return
        oid = item.data(Qt.ItemDataRole.UserRole)
        self._steps[oid] = replace(
            self._steps[oid], action=self.action.currentData(), message=self.message.toPlainText()
        )
        self._label(item)
        self._validate()

    def _move(self, offset):
        row = self.commits.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.commits.count():
            return
        item = self.commits.takeItem(row)
        self.commits.insertItem(target, item)
        self.commits.setCurrentRow(target)
        self._validate()

    def steps(self):
        if not self.interactive.isChecked():
            return None
        return tuple(
            self._steps[self.commits.item(row).data(Qt.ItemDataRole.UserRole)]
            for row in range(self.commits.count())
        )

    def _validate(self, *_):
        editing = self.interactive.isChecked() and self.preview is not None
        row = self.commits.currentRow()
        self.up.setEnabled(editing and row > 0)
        self.down.setEnabled(editing and 0 <= row < self.commits.count() - 1)
        self.action.setEnabled(editing and row >= 0)
        self.message.setReadOnly(not editing or self.action.currentData() != "reword")
        self.commits.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
            if editing
            else QAbstractItemView.DragDropMode.NoDragDrop
        )
        self.start.setEnabled(False)
        if self.preview is None:
            return
        self.error.clear()
        if editing:
            try:
                self.service.validate_steps(self.preview, self.steps())
            except ValueError as exc:
                self.error.setText(str(exc))
                return
        self.start.setEnabled(True)

    def accept(self):
        self._validate()
        if self.start.isEnabled():
            super().accept()
