"""One action per command, shared by menus, keyboard shortcuts, and the palette."""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout


@dataclass
class Command:
    action: QAction
    run: Callable[[], None]
    available: Callable[[], bool]


class CommandRegistry(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.commands: dict[str, Command] = {}

    def register(self, key, label, run, *, available=lambda: True, shortcut=None):
        if key in self.commands:
            raise ValueError(f"Duplicate command: {key}")
        action = QAction(label, self)
        action.setObjectName(f"command.{key}")
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: self.execute(key))
        self.commands[key] = Command(action, run, available)
        self.window.addAction(action)
        return action

    def action(self, key):
        return self.commands[key].action

    @Slot()
    def refresh(self):
        for command in self.commands.values():
            command.action.setEnabled(bool(command.available()))

    def execute(self, key):
        # Recheck at invocation: a shortcut or palette selection can become stale
        # while another control starts an operation.
        command = self.commands[key]
        if not command.available():
            self.refresh()
            return False
        try:
            command.run()
        finally:
            self.refresh()
        return True


class CommandPalette(QDialog):
    def __init__(self, registry: CommandRegistry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Commands")
        self.resize(560, 400)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a command…")
        self.search.setAccessibleName("Find a command")
        layout.addWidget(self.search)
        layout.addWidget(QLabel("Unavailable commands are disabled for the current view."))
        self.results = QListWidget()
        self.results.setAccessibleName("Matching commands")
        layout.addWidget(self.results)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._activate)
        self.results.itemActivated.connect(self._activate)
        self._filter()

    def _filter(self):
        self.registry.refresh()
        self.results.clear()
        needle = self.search.text().casefold()
        for key, command in self.registry.commands.items():
            if key == "palette" or needle not in command.action.text().casefold():
                continue
            shortcut = command.action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            text = command.action.text() + (f"    {shortcut}" if shortcut else "")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, key)
            if not command.action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.results.addItem(item)
        for index in range(self.results.count()):
            if self.results.item(index).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.results.setCurrentRow(index)
                break

    def _activate(self, *_args):
        item = self.results.currentItem()
        if item is None or not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not self.registry.commands[key].available():
            self._filter()
            return
        self.accept()
        self.registry.execute(key)
