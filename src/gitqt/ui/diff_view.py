"""Inline diff viewer widget with syntax-colored hunks and staging actions."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gitqt.parsers.diff import DiffHunk, DiffLineType
from gitqt.ui.theme import (
    DIFF_ADD_BG,
    DIFF_ADD_FG,
    DIFF_CONTEXT_FG,
    DIFF_DEL_BG,
    DIFF_DEL_FG,
    DIFF_META_FG,
    mono_font,
)


class DiffView(QWidget):
    """Renders a list of DiffHunks with coloured additions/deletions."""

    selection_changed = Signal()
    stage_hunk_requested = Signal(int)
    discard_hunk_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QLabel()
        self._header.setProperty("subheading", True)
        self._header.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self._header)
        self._notice = QLabel()
        self._notice.setWordWrap(True)
        self._notice.setProperty("dim", True)
        self._notice.setContentsMargins(8, 4, 8, 4)
        self._notice.hide()
        layout.addWidget(self._notice)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(mono_font(11))
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setStyleSheet(
            "QPlainTextEdit { background: #181825; border: none; border-radius: 0; }"
        )
        layout.addWidget(self._text)

        self._empty_label = QLabel("Select a file to view its diff.")
        self._empty_label.setProperty("dim", True)
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setContentsMargins(0, 40, 0, 40)
        layout.addWidget(self._empty_label)

        self._hunks: list[DiffHunk] = []
        self._blocks: dict[int, tuple[int, int]] = {}
        self._text.selectionChanged.connect(self.selection_changed)
        self._text.cursorPositionChanged.connect(self.selection_changed)
        self._show_empty(True)

    def set_diff(self, file_path: str | None, hunks: list[DiffHunk], message: str = "") -> None:
        if sum(len(hunk.lines) for hunk in hunks) > 10000:
            hunks = []
            message = "This diff exceeds the 10,000-line display limit. Use an external viewer."
        self._hunks = hunks
        self._notice.setText(message)
        self._notice.setVisible(bool(message and hunks))
        self._blocks = {}
        self._empty_label.setText(message or "Select a file to view its diff.")
        self._header.setText(file_path or "")
        if not hunks or not file_path:
            self._text.clear()
            self._show_empty(True)
            return

        self._header.setText(file_path)
        self._show_empty(False)
        self._render_hunks(hunks)

    def clear(self) -> None:
        self._hunks = []
        self._notice.hide()
        self._blocks = {}
        self._empty_label.setText("Select a file to view its diff.")
        self._header.setText("")
        self._text.clear()
        self._show_empty(True)

    def _show_empty(self, empty: bool) -> None:
        self._text.setVisible(not empty)
        self._empty_label.setVisible(empty)

    def _render_hunks(self, hunks: list[DiffHunk]) -> None:
        self._text.clear()
        cursor = self._text.textCursor()
        cursor.beginEditBlock()

        for i, hunk in enumerate(hunks):
            if i > 0:
                self._insert_line(cursor, "", _meta_format())

            self._insert_line(cursor, hunk.header, _meta_format())
            self._blocks[cursor.blockNumber()] = (i, -1)

            for line_index, line in enumerate(hunk.lines):
                prefix, fmt = _format_for_line_type(line.type)
                num_text = _line_number_text(line.old_line_number, line.new_line_number)
                self._insert_line(
                    cursor, f"{num_text} {prefix}{line.text.removesuffix(chr(13))}", fmt
                )
                self._blocks[cursor.blockNumber()] = (i, line_index)

        cursor.endEditBlock()
        self._text.setTextCursor(cursor)
        self._text.moveCursor(QTextCursor.MoveOperation.Start)

    def current_hunk(self) -> int:
        return self._blocks.get(self._text.textCursor().blockNumber(), (-1, -1))[0]

    def select_hunk(self, index: int) -> None:
        for block_number, position in self._blocks.items():
            if position == (index, -1):
                cursor = QTextCursor(self._text.document().findBlockByNumber(block_number))
                self._text.setTextCursor(cursor)
                self._text.ensureCursorVisible()
                return

    def selected_changes(self) -> tuple[int, set[int]] | None:
        cursor = self._text.textCursor()
        if not cursor.hasSelection():
            return None
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        found: dict[int, set[int]] = {}
        for block_number, (hunk_index, line_index) in self._blocks.items():
            block = self._text.document().findBlockByNumber(block_number)
            if block.position() >= end or block.position() + block.length() - 1 < start:
                continue
            if line_index >= 0 and self._hunks[hunk_index].lines[line_index].type in {
                DiffLineType.ADDITION,
                DiffLineType.DELETION,
            }:
                found.setdefault(hunk_index, set()).add(line_index)
        if len(found) != 1:
            return (-1, set())
        return next(iter(found.items()))

    @staticmethod
    def _insert_line(cursor: QTextCursor, text: str, fmt: QTextCharFormat) -> None:
        if not cursor.atStart():
            cursor.insertText("\n", fmt)
        cursor.insertText(text, fmt)


class DiffToolbar(QWidget):
    """Small toolbar shown above the diff for staging/discarding hunks."""

    hunk_selected = Signal(int)
    stage_hunk_clicked = Signal()
    discard_hunk_clicked = Signal()
    external_diff_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._hunk_picker = QComboBox()
        self._hunk_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._hunk_picker.setMaximumWidth(230)
        self._hunk_picker.setMinimumContentsLength(8)
        self._hunk_picker.setToolTip("Choose a hunk, or highlight changed lines within one hunk.")
        self._hunk_picker.currentIndexChanged.connect(self.hunk_selected)
        layout.addWidget(self._hunk_picker)

        self._stage_btn = QPushButton("Stage Hunk")
        self._stage_btn.setToolTip("Stage the selected hunk or highlighted changed lines")
        self._stage_btn.clicked.connect(self.stage_hunk_clicked)
        layout.addWidget(self._stage_btn)

        self._discard_btn = QPushButton("Discard Hunk")
        self._discard_btn.setProperty("danger", True)
        self._discard_btn.setToolTip("Discard changes in this hunk")
        self._discard_btn.clicked.connect(self.discard_hunk_clicked)
        layout.addWidget(self._discard_btn)

        layout.addStretch()

        self._external_btn = QPushButton("External Diff")
        self._external_btn.setToolTip("Open in configured external diff tool")
        self._external_btn.clicked.connect(self.external_diff_clicked)
        layout.addWidget(self._external_btn)


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _meta_format() -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(DIFF_META_FG)
    fmt.setFont(mono_font(11))
    return fmt


def _format_for_line_type(line_type: DiffLineType) -> tuple[str, QTextCharFormat]:
    fmt = QTextCharFormat()
    fmt.setFont(mono_font(11))

    if line_type == DiffLineType.ADDITION:
        fmt.setForeground(DIFF_ADD_FG)
        fmt.setBackground(DIFF_ADD_BG)
        return "+", fmt
    if line_type == DiffLineType.DELETION:
        fmt.setForeground(DIFF_DEL_FG)
        fmt.setBackground(DIFF_DEL_BG)
        return "-", fmt
    if line_type == DiffLineType.META:
        fmt.setForeground(DIFF_META_FG)
        return " ", fmt

    fmt.setForeground(DIFF_CONTEXT_FG)
    return " ", fmt


def _line_number_text(old: int | None, new: int | None) -> str:
    old_str = str(old).rjust(4) if old is not None else "    "
    new_str = str(new).rjust(4) if new is not None else "    "
    return f"{old_str} {new_str}"
