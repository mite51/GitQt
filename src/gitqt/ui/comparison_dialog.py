"""Read-only comparison workspace, deliberately separate from patch staging widgets."""

from __future__ import annotations

import keyword
import re
from difflib import SequenceMatcher
from itertools import zip_longest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gitqt.parsers.diff import DiffLine, DiffLineType
from gitqt.services.comparison import (
    Comparison,
    ComparisonDiff,
    ComparisonRequest,
    ComparisonService,
    DiffOptions,
)
from gitqt.services.session_jobs import SessionJobs
from gitqt.ui.theme import mono_font

_SYNTAX = re.compile(
    r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b[A-Za-z_]\w*\b|\b\d+(?:\.\d+)?\b|\#.*$|//.*$)"""
)
_KEYWORDS = set(keyword.kwlist) | {
    "const",
    "let",
    "var",
    "function",
    "return",
    "class",
    "new",
    "null",
    "true",
    "false",
    "import",
    "export",
    "public",
    "private",
    "void",
    "int",
    "string",
    "async",
    "await",
}
_CODE_SUFFIXES = {".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".java", ".c", ".cpp", ".h"}


def _word_ranges(old: str, new: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Find changed tokens, with a strict per-line bound for pathological generated files."""
    if max(len(old), len(new)) > 4096:
        return [], []
    tokens = [list(re.finditer(r"\w+|\s+|[^\w\s]", value)) for value in (old, new)]
    ranges: tuple[list[tuple[int, int]], list[tuple[int, int]]] = ([], [])
    matcher = SequenceMatcher(None, [m[0] for m in tokens[0]], [m[0] for m in tokens[1]])
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == "equal":
            continue
        for side, start, end in ((0, a, b), (1, c, d)):
            if start < end:
                ranges[side].append((tokens[side][start].start(), tokens[side][end - 1].end()))
    return ranges


def _paired_lines(lines: list[DiffLine]):
    index = 0
    changed = {DiffLineType.DELETION, DiffLineType.ADDITION}
    while index < len(lines):
        line = lines[index]
        if line.type in changed:
            removed, added = [], []
            while index < len(lines) and lines[index].type in changed:
                current = lines[index]
                index += 1
                marker = None
                if (
                    index < len(lines)
                    and lines[index].type == DiffLineType.META
                    and lines[index].text.startswith("\\ No newline")
                ):
                    marker = lines[index]
                    index += 1
                (removed if current.type == DiffLineType.DELETION else added).append(
                    (current, marker)
                )
            for old, new in zip_longest(removed, added, fillvalue=(None, None)):
                yield old[0], new[0]
                if old[1] or new[1]:
                    yield old[1], new[1]
        else:
            yield line, line
            index += 1


class ComparisonRenderer(QWidget):
    """Display-only transformations: never produces or applies repository patches."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._splitter = QSplitter()
        self.left = QPlainTextEdit()
        self.right = QPlainTextEdit()
        for editor in (self.left, self.right):
            editor.setReadOnly(True)
            editor.setFont(mono_font(10))
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self._splitter.addWidget(editor)
        layout.addWidget(self._splitter)
        self.left.verticalScrollBar().valueChanged.connect(self.right.verticalScrollBar().setValue)
        self.right.verticalScrollBar().valueChanged.connect(self.left.verticalScrollBar().setValue)
        self._changes: list[int] = []
        self._diff: ComparisonDiff | None = None
        self._side_by_side = False
        self._word_diff = True
        self._syntax = False
        self._search_editor = self.left
        self._format_budget = 20_000
        self.right.hide()

    def set_diff(self, diff: ComparisonDiff | None, *, side_by_side=False, word_diff=True):
        self._diff = diff
        self._side_by_side = side_by_side
        self._word_diff = word_diff
        self._search_editor = self.left
        self._format_budget = 20_000
        self._changes.clear()
        self.left.clear()
        self.right.clear()
        self.right.setVisible(side_by_side)
        if diff is None:
            return
        detailed = len(diff.raw) <= 250_000
        self._word_diff = word_diff and detailed
        self._syntax = detailed and Path(diff.path).suffix.lower() in _CODE_SUFFIXES
        if not diff.hunks:
            self.left.setPlainText(diff.raw or diff.notice)
            return
        left, right = self.left.textCursor(), self.right.textCursor()
        left.beginEditBlock()
        right.beginEditBlock()
        for hunk in diff.hunks:
            self._line(left, hunk.header, "", DiffLineType.META)
            self._changes.append(left.blockNumber())
            if side_by_side:
                self._line(right, hunk.header, "", DiffLineType.META)
            pairs = list(_paired_lines(hunk.lines))
            if side_by_side:
                for old, new in pairs:
                    ranges = self._ranges(old, new)
                    for cursor, line, number, spans in (
                        (left, old, old.old_line_number if old else None, ranges[0]),
                        (right, new, new.new_line_number if new else None, ranges[1]),
                    ):
                        self._line(
                            cursor,
                            line.text if line else "",
                            f"{number:>5}  " if number is not None else "       ",
                            line.type if line else DiffLineType.CONTEXT,
                            spans,
                        )
            else:
                spans_by_id = {}
                for old, new in pairs:
                    old_ranges, new_ranges = self._ranges(old, new)
                    if old:
                        spans_by_id[id(old)] = old_ranges
                    if new:
                        spans_by_id[id(new)] = new_ranges
                for line in hunk.lines:
                    old = str(line.old_line_number or "").rjust(5)
                    new = str(line.new_line_number or "").rjust(5)
                    prefix = {DiffLineType.ADDITION: "+", DiffLineType.DELETION: "-"}.get(
                        line.type, " "
                    )
                    self._line(
                        left,
                        line.text,
                        f"{old} {new} {prefix}",
                        line.type,
                        spans_by_id.get(id(line), []),
                    )
        left.endEditBlock()
        right.endEditBlock()
        for editor in (self.left, self.right):
            editor.moveCursor(QTextCursor.MoveOperation.Start)

    def _ranges(self, old, new):
        if (
            self._word_diff
            and old
            and new
            and old.type == DiffLineType.DELETION
            and new.type == DiffLineType.ADDITION
        ):
            return _word_ranges(old.text, new.text)
        return [], []

    def _line(self, cursor, text, prefix, kind, ranges=()):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#cdd6f4"))
        if kind == DiffLineType.ADDITION:
            fmt.setBackground(QColor("#203d2b"))
        elif kind == DiffLineType.DELETION:
            fmt.setBackground(QColor("#4b2830"))
        elif kind == DiffLineType.META:
            fmt.setForeground(QColor("#89b4fa"))
        if not cursor.atStart():
            cursor.insertText("\n", fmt)
        cursor.insertText(prefix, fmt)
        # CR is shown explicitly; the underlying Git patch is never rewritten.
        visible = text.replace("\r", "␍")
        if len(visible) > 4096 or self._format_budget <= 0:
            cursor.insertText(visible, fmt)
            return
        tokens = list(_SYNTAX.finditer(visible)) if self._syntax else []
        boundaries = {0, len(visible)}
        for start, end in ranges:
            boundaries.update((start, end))
        for token in tokens:
            boundaries.update(token.span())
        points = sorted(boundaries)
        self._format_budget -= len(points)
        token_index = 0
        for start, end in zip(points, points[1:], strict=False):
            part = QTextCharFormat(fmt)
            while token_index < len(tokens) and tokens[token_index].end() <= start:
                token_index += 1
            if token_index < len(tokens) and tokens[token_index].start() <= start:
                token = tokens[token_index][0]
                if token.startswith(("#", "//")):
                    part.setForeground(QColor("#9399b2"))
                elif token.startswith(('"', "'")):
                    part.setForeground(QColor("#a6e3a1"))
                elif token in _KEYWORDS:
                    part.setForeground(QColor("#cba6f7"))
                elif token[0].isdigit():
                    part.setForeground(QColor("#fab387"))
            if any(a <= start < b for a, b in ranges):
                part.setBackground(
                    QColor("#27683c" if kind == DiffLineType.ADDITION else "#873948")
                )
            cursor.insertText(visible[start:end], part)

    def next_change(self):
        if not self._changes:
            return
        current = self.left.textCursor().blockNumber()
        target = next((block for block in self._changes if block > current), self._changes[0])
        for editor in (self.left, self.right) if self._side_by_side else (self.left,):
            editor.setTextCursor(QTextCursor(editor.document().findBlockByNumber(target)))
            editor.ensureCursorVisible()

    def find(self, text: str) -> bool:
        if not text:
            return False
        editors = (self.left, self.right) if self._side_by_side else (self.left,)
        current = self._search_editor
        if current.find(text):
            current.setFocus()
            return True
        others = [editor for editor in editors if editor is not current]
        for editor in [*others, current]:
            editor.moveCursor(QTextCursor.MoveOperation.Start)
            if editor.find(text):
                self._search_editor = editor
                editor.setFocus()
                return True
        return False


class ComparisonDialog(QDialog):
    def __init__(
        self,
        service: ComparisonService,
        repository_path: str,
        parent=None,
        *,
        base="HEAD",
        target="HEAD",
        commit=None,
        pool=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare changes")
        self.resize(1200, 820)
        self._service = service
        self._repository_path = repository_path
        self._jobs = SessionJobs(pool=pool, parent=self)
        self._jobs.activate(repository_path)
        self._comparison: Comparison | None = None
        self._diff: ComparisonDiff | None = None
        self._requested_commit = commit
        layout = QVBoxLayout(self)
        endpoints = QHBoxLayout()
        self.base = QComboBox()
        self.target = QComboBox()
        for combo, value in ((self.base, base), (self.target, commit or target)):
            combo.setEditable(True)
            combo.addItem(value)
            combo.setMinimumWidth(170)
        self.kind = QComboBox()
        self.kind.addItem("Commit / reference", "commit")
        self.kind.addItem("Staged snapshot", "index")
        self.kind.addItem("Working tree", "worktree")
        self.kind.setCurrentIndex(0 if commit else 2)
        self.mode = QComboBox()
        self.mode.addItem("Direct endpoints", False)
        self.mode.addItem("Merge base → target", True)
        self.compare_button = QPushButton("Compare")
        for widget in (
            QLabel("Base"),
            self.base,
            QLabel("Target"),
            self.kind,
            self.target,
            self.mode,
            self.compare_button,
        ):
            endpoints.addWidget(widget)
        layout.addLayout(endpoints)
        self.direction = QLabel(
            "Direct: base → target. Merge base: common ancestor of both refs → target."
        )
        self.direction.setWordWrap(True)
        self.direction.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.direction)
        parent_row = QHBoxLayout()
        parent_row.addWidget(QLabel("Commit comparison"))
        self.parent_picker = QComboBox()
        self.parent_picker.addItem("Use selected base", None)
        parent_row.addWidget(self.parent_picker)
        parent_row.addStretch()
        layout.addLayout(parent_row)
        self.message = QPlainTextEdit()
        self.message.setReadOnly(True)
        self.message.setPlaceholderText("Full target commit message appears here.")
        self.message.setMaximumHeight(120)
        layout.addWidget(self.message)
        options = QHBoxLayout()
        self.side_by_side = QCheckBox("Side by side")
        self.word_diff = QCheckBox("Highlight changed words")
        self.word_diff.setChecked(True)
        self.whitespace = QComboBox()
        self.whitespace.addItem("Exact whitespace", "exact")
        self.whitespace.addItem("Ignore whitespace amount", "space-change")
        self.whitespace.addItem("Ignore all whitespace", "all-space")
        self.context = QSpinBox()
        self.context.setRange(0, 1000)
        self.context.setValue(3)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find in diff")
        self.find_button = QPushButton("Find next")
        self.next_button = QPushButton("Next change")
        for widget in (
            self.side_by_side,
            self.word_diff,
            self.whitespace,
            QLabel("Context"),
            self.context,
            self.search,
            self.find_button,
            self.next_button,
        ):
            options.addWidget(widget)
        layout.addLayout(options)
        splitter = QSplitter()
        self.files = QListWidget()
        self.files.setMinimumWidth(220)
        splitter.addWidget(self.files)
        self.renderer = ComparisonRenderer()
        splitter.addWidget(self.renderer)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.compare_button.clicked.connect(self.compare)
        self.parent_picker.currentIndexChanged.connect(self.compare)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.base.editTextChanged.connect(self._invalidate)
        self.target.editTextChanged.connect(self._target_changed)
        self.mode.currentIndexChanged.connect(self._invalidate)
        self.files.currentItemChanged.connect(self._load_file)
        self.whitespace.currentIndexChanged.connect(self._load_file)
        self.context.valueChanged.connect(self._load_file)
        self.side_by_side.toggled.connect(self._render)
        self.word_diff.toggled.connect(self._render)
        self.find_button.clicked.connect(self._find)
        self.search.returnPressed.connect(self._find)
        self.next_button.clicked.connect(self.renderer.next_change)
        self.finished.connect(lambda _result: self._jobs.invalidate_reads())
        self._kind_changed()
        self._jobs.read(
            "refs", lambda: self._service.references(repository_path), self._set_refs, self._error
        )
        self.compare()

    def _set_refs(self, refs):
        for combo in (self.base, self.target):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(refs)
            if combo is self.base:
                combo.addItem("INDEX")
            combo.setCurrentText(current)
            combo.blockSignals(False)
        self.base.setToolTip(
            "A full reference or commit ID; INDEX compares unstaged changes "
            "against the working tree."
        )

    def _invalidate(self, *_args):
        self._jobs.invalidate_channel("comparison")
        self._jobs.invalidate_channel("diff")
        self._comparison = None
        self._diff = None
        self.files.clear()
        self.renderer.set_diff(None)
        self.status.setText("Choose endpoints and press Compare.")

    def _target_changed(self, *_args):
        self._requested_commit = None
        self.parent_picker.blockSignals(True)
        self.parent_picker.clear()
        self.parent_picker.addItem("Use selected base", None)
        self.parent_picker.blockSignals(False)
        self.message.clear()
        self.mode.setEnabled(self.kind.currentData() == "commit")
        self.base.setEnabled(True)
        self._invalidate()

    def _kind_changed(self, *_args):
        is_commit = self.kind.currentData() == "commit"
        self.target.setEnabled(is_commit)
        self.mode.setEnabled(is_commit and self.parent_picker.currentData() is None)
        self.base.setEnabled(not is_commit or self.parent_picker.currentData() is None)
        self.parent_picker.setEnabled(is_commit)
        self.message.setVisible(is_commit)
        self._invalidate()

    def compare(self, *_args):
        self._jobs.invalidate_channel("diff")
        self._comparison = None
        self._diff = None
        self.files.clear()
        self.renderer.set_diff(None)
        kind = self.kind.currentData()
        parent = self.parent_picker.currentData() if kind == "commit" else None
        request = ComparisonRequest(
            self.base.currentText(),
            self.target.currentText(),
            kind,
            bool(self.mode.currentData()) and kind == "commit" and parent is None,
            parent,
        )
        self.status.setText("Loading comparison…")
        initial_commit = self._requested_commit
        self._requested_commit = None

        def load():
            if initial_commit:
                details = self._service.commit_details(self._repository_path, initial_commit)
                return self._service.compare(
                    self._repository_path,
                    ComparisonRequest(target=details.oid, parent=1 if details.parents else 0),
                )
            return self._service.compare(self._repository_path, request)

        def apply(result):
            self._comparison = result
            self.direction.setText(result.description)
            self.message.setPlainText(result.commit.message if result.commit else "")
            self.parent_picker.blockSignals(True)
            self.parent_picker.clear()
            self.parent_picker.addItem("Use selected base", None)
            if result.commit:
                if result.commit.parents:
                    for index, oid in enumerate(result.commit.parents, 1):
                        self.parent_picker.addItem(f"Parent {index}: {oid[:12]}", index)
                else:
                    self.parent_picker.addItem("Empty tree (root commit)", 0)
            selected = (1 if result.commit.parents else 0) if initial_commit else parent
            if selected is not None:
                self.parent_picker.setCurrentIndex(self.parent_picker.findData(selected))
            self.parent_picker.blockSignals(False)
            self.mode.setEnabled(kind == "commit" and self.parent_picker.currentData() is None)
            self.base.setEnabled(kind != "commit" or self.parent_picker.currentData() is None)
            for change in result.files:
                label = f"{change.status}  {change.path}"
                if change.old_path:
                    label += f"  ← {change.old_path}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, change)
                self.files.addItem(item)
            self.status.setText(f"{len(result.files)} changed files. Read-only comparison.")
            if self.files.count():
                self.files.setCurrentRow(0)

        self._jobs.read("comparison", load, apply, self._error)

    def _load_file(self, *_args):
        self._jobs.invalidate_channel("diff")
        self._diff = None
        self.renderer.set_diff(None)
        item = self.files.currentItem()
        if self._comparison is None or item is None:
            return
        change = item.data(Qt.ItemDataRole.UserRole)
        comparison = self._comparison
        options = DiffOptions(self.context.value(), self.whitespace.currentData())
        self.status.setText(f"Loading {change.path}…")

        def apply(diff):
            self._diff = diff
            self.status.setText(
                diff.notice or f"{change.path} — Read-only comparison. CR is shown as ␍."
            )
            self._render()

        self._jobs.read(
            "diff",
            lambda: self._service.file_diff(self._repository_path, comparison, change, options),
            apply,
            self._error,
        )

    def _render(self, *_args):
        self.renderer.set_diff(
            self._diff,
            side_by_side=self.side_by_side.isChecked(),
            word_diff=self.word_diff.isChecked(),
        )

    def _find(self):
        if not self.renderer.find(self.search.text()):
            self.status.setText("Text not found in the displayed diff.")

    def _error(self, error):
        self.status.setText(str(error.exception))
