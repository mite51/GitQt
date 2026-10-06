"""Collapsible operation log panel shown at the bottom of the repository view."""

from __future__ import annotations

from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gitqt.app.remote_operations import OperationLogEntry
from gitqt.ui.theme import ERROR, FG_DIM, FG_PRIMARY, WARNING, mono_font


class OperationLogPanel(QWidget):
    """Persistent, collapsible log of Git operations and their outcomes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(10, 4, 10, 4)
        self._toggle_btn = QPushButton("Operation Log")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setStyleSheet(
            "QPushButton { text-align: left; font-weight: 600; color: #a6adc8; "
            "border: none; padding: 2px 0; }"
        )
        self._toggle_btn.clicked.connect(self._toggle_body)
        header.addWidget(self._toggle_btn, stretch=1)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(24)
        self._clear_btn.setStyleSheet("QPushButton { padding: 2px 10px; font-size: 10pt; }")
        self._clear_btn.clicked.connect(self.clear)
        header.addWidget(self._clear_btn)

        self._hide_btn = QPushButton("Hide")
        self._hide_btn.setFixedHeight(24)
        self._hide_btn.setStyleSheet("QPushButton { padding: 2px 10px; font-size: 10pt; }")
        self._hide_btn.clicked.connect(self._toggle_body)
        header.addWidget(self._hide_btn)

        header_widget = QWidget()
        header_widget.setLayout(header)
        header_widget.setStyleSheet(
            "QWidget { background: #313244; border-top: 1px solid #45475a; }"
        )
        layout.addWidget(header_widget)

        self._body = QPlainTextEdit()
        self._body.setReadOnly(True)
        self._body.setMaximumBlockCount(500)
        self._body.setFont(mono_font(10))
        self._body.setMaximumHeight(160)
        self._body.setStyleSheet(
            "QPlainTextEdit { background: #181825; border: none; "
            "border-radius: 0; padding: 4px 8px; }"
        )
        layout.addWidget(self._body)

        self._collapsed = True
        self._body.setVisible(False)

    def set_entries(self, entries: list[OperationLogEntry]) -> None:
        self._body.clear()
        cursor = self._body.textCursor()
        cursor.beginEditBlock()
        for entry in entries:
            fmt = _format_for_level(entry.level)
            ts = entry.timestamp.strftime("%H:%M:%S")
            level_tag = entry.level.upper().ljust(5)
            text = f"{ts} {level_tag} {entry.summary}"
            if not cursor.atStart():
                cursor.insertText("\n", fmt)
            cursor.insertText(text, fmt)
        cursor.endEditBlock()
        self._body.setTextCursor(cursor)
        self._body.moveCursor(QTextCursor.MoveOperation.End)

    def append_message(self, level: str, message: str) -> None:
        fmt = _format_for_level(level)
        cursor = self._body.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not cursor.atStart():
            cursor.insertText("\n", fmt)
        cursor.insertText(message, fmt)
        self._body.setTextCursor(cursor)
        self._body.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self) -> None:
        self._body.clear()

    def expand(self) -> None:
        self._collapsed = False
        self._body.setVisible(True)
        self._hide_btn.setText("Hide")

    def collapse(self) -> None:
        self._collapsed = True
        self._body.setVisible(False)
        self._hide_btn.setText("Show")

    def _toggle_body(self) -> None:
        if self._collapsed:
            self.expand()
        else:
            self.collapse()


def _format_for_level(level: str) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setFont(mono_font(10))
    normalized = level.lower()
    if normalized in ("error", "err"):
        fmt.setForeground(ERROR)
    elif normalized in ("warn", "warning"):
        fmt.setForeground(WARNING)
    elif normalized in ("info",):
        fmt.setForeground(FG_PRIMARY)
    else:
        fmt.setForeground(FG_DIM)
    return fmt
