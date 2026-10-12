"""Working tree panel: file status list, diff viewer, and commit composer."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gitqt.app.working_tree import WorkingTreeStateModel, WorkingTreeViewModel
from gitqt.parsers.status import WorkingTreeEntry, WorkingTreeState
from gitqt.services.session_jobs import SessionJobs
from gitqt.ui.diff_view import DiffToolbar, DiffView
from gitqt.ui.theme import (
    ACCENT,
    ERROR,
    FG_DIM,
    STATUS_ADDED,
    STATUS_DELETED,
    STATUS_MODIFIED,
    STATUS_UNTRACKED,
    WARNING,
    ui_font,
)

_STATE_ICONS = {
    WorkingTreeState.MODIFIED: ("M", STATUS_MODIFIED),
    WorkingTreeState.ADDED: ("A", STATUS_ADDED),
    WorkingTreeState.DELETED: ("D", STATUS_DELETED),
    WorkingTreeState.UNTRACKED: ("?", STATUS_UNTRACKED),
}


class WorkingTreeView(QWidget):
    """Main working-tree panel: file list, diff, and commit composer."""

    refresh_requested = Signal()
    external_diff_requested = Signal()
    external_merge_requested = Signal()
    commit_succeeded = Signal()
    working_tree_changed = Signal()
    operation_action_requested = Signal(str, str)
    action_message = Signal(str)

    _SEARCH_DEBOUNCE_MS = 200

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_model: WorkingTreeViewModel | None = None
        self._jobs = SessionJobs(parent=self)
        self._jobs.busy_changed.connect(self._set_busy)
        self._search_text = ""
        self._pending_search_text: str | None = None
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(self._SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._commit_search_change)
        self._last_staged_signature: tuple[tuple[str, str], ...] | None = None
        self._last_unstaged_signature: tuple[tuple[str, str], ...] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 6, 8, 6)

        top_label = QLabel("Working Tree")
        top_label.setFont(ui_font(12, bold=True))
        top_bar.addWidget(top_label)
        top_bar.addStretch()

        self._status_label = QLabel()
        self._status_label.setProperty("dim", True)
        top_bar.addWidget(self._status_label)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setFixedHeight(26)
        self._refresh_btn.clicked.connect(self.refresh_requested)
        top_bar.addWidget(self._refresh_btn)

        layout.addLayout(top_bar)

        # Main splitter: file list | diff
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: file list panel
        file_panel = QWidget()
        file_layout = QVBoxLayout(file_panel)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search files...")
        self._search.setContentsMargins(6, 2, 6, 2)
        self._search.textChanged.connect(self._on_search_changed)
        file_layout.addWidget(self._search)

        # Filter buttons
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(6, 0, 6, 0)
        filter_row.setSpacing(4)
        self._filter_buttons: dict[WorkingTreeState, QPushButton] = {}
        for state in WorkingTreeState:
            btn = QPushButton(state.value.capitalize())
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setFixedHeight(24)
            btn.setStyleSheet("""
                QPushButton { font-size: 9pt; padding: 2px 8px; border-radius: 4px; }
                QPushButton:checked { background: #45475a; color: #cdd6f4; }
                QPushButton:!checked { background: #181825; color: #6c7086; }
            """)
            btn.clicked.connect(self._on_filter_toggled)
            filter_row.addWidget(btn)
            self._filter_buttons[state] = btn
        filter_row.addStretch()
        file_layout.addLayout(filter_row)

        # Staged files
        self._staged_label = QLabel("  Staged")
        self._staged_label.setFont(ui_font(10, bold=True))
        self._staged_label.setStyleSheet(f"color: {ACCENT.name()}; padding: 4px 6px;")
        file_layout.addWidget(self._staged_label)

        self._staged_list = QListWidget()
        self._staged_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._staged_list.setMaximumHeight(160)
        self._staged_list.currentItemChanged.connect(
            lambda current, _: self._on_file_selected(current)
        )
        file_layout.addWidget(self._staged_list)

        # Unstaged files
        self._unstaged_label = QLabel("  Unstaged")
        self._unstaged_label.setFont(ui_font(10, bold=True))
        self._unstaged_label.setStyleSheet(f"color: {WARNING.name()}; padding: 4px 6px;")
        file_layout.addWidget(self._unstaged_label)

        self._unstaged_list = QListWidget()
        self._unstaged_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._unstaged_list.currentItemChanged.connect(
            lambda current, _: self._on_file_selected(current)
        )
        file_layout.addWidget(self._unstaged_list)

        # Stage/unstage buttons
        action_row = QHBoxLayout()
        action_row.setContentsMargins(6, 4, 6, 4)
        action_row.setSpacing(6)

        self._stage_btn = QPushButton("Stage Selected")
        self._stage_btn.clicked.connect(self._stage_selected)
        action_row.addWidget(self._stage_btn)

        self._unstage_btn = QPushButton("Unstage Selected")
        self._unstage_btn.clicked.connect(self._unstage_selected)
        action_row.addWidget(self._unstage_btn)

        file_layout.addLayout(action_row)
        all_row = QHBoxLayout()
        self._stage_all_btn = QPushButton("Stage All")
        self._unstage_all_btn = QPushButton("Unstage All")
        self._stage_all_btn.setToolTip(
            "Stage all changed files, including files hidden by filters."
        )
        self._unstage_all_btn.setToolTip("Unstage all files, including files hidden by filters.")
        self._stage_all_btn.clicked.connect(lambda: self._change_files(stage=True, all_files=True))
        self._unstage_all_btn.clicked.connect(
            lambda: self._change_files(stage=False, all_files=True)
        )
        all_row.addWidget(self._stage_all_btn)
        all_row.addWidget(self._unstage_all_btn)
        file_layout.addLayout(all_row)

        self._main_splitter.addWidget(file_panel)

        # Right: diff panel
        diff_panel = QWidget()
        diff_layout = QVBoxLayout(diff_panel)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        diff_layout.setSpacing(0)

        self._diff_toolbar = DiffToolbar()
        self._diff_toolbar.stage_hunk_clicked.connect(self._stage_hunk)
        self._diff_toolbar.discard_hunk_clicked.connect(self._discard_hunk)
        self._diff_toolbar.external_diff_clicked.connect(self.external_diff_requested)
        diff_layout.addWidget(self._diff_toolbar)

        self._diff_view = DiffView()
        self._diff_toolbar.hunk_selected.connect(self._diff_view.select_hunk)
        self._diff_view.selection_changed.connect(self._sync_diff_actions)
        diff_layout.addWidget(self._diff_view)

        self._main_splitter.addWidget(diff_panel)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 2)
        layout.addWidget(self._main_splitter, stretch=1)

        # Conflict banner
        self._conflict_banner = QWidget()
        banner_layout = QHBoxLayout(self._conflict_banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        self._conflict_banner.setStyleSheet(
            "QWidget { background: rgba(243, 139, 168, 30); border-top: 2px solid #f38ba8; }"
        )
        self._conflict_icon = QLabel("⚠")
        self._conflict_icon.setFont(ui_font(14))
        banner_layout.addWidget(self._conflict_icon)
        self._conflict_text = QLabel()
        self._conflict_text.setWordWrap(True)
        banner_layout.addWidget(self._conflict_text, stretch=1)
        self._merge_tool_btn = QPushButton("Open Merge Tool")
        self._merge_tool_btn.clicked.connect(self.external_merge_requested)
        banner_layout.addWidget(self._merge_tool_btn)
        self._continue_btn = QPushButton("Continue")
        self._skip_btn = QPushButton("Skip Commit")
        self._abort_btn = QPushButton("Abort")
        for button, action in (
            (self._continue_btn, "continue"),
            (self._skip_btn, "skip"),
            (self._abort_btn, "abort"),
        ):
            button.clicked.connect(
                lambda _checked=False, value=action: self._operation_action(value)
            )
            banner_layout.addWidget(button)
        layout.addWidget(self._conflict_banner)
        self._conflict_banner.setVisible(False)

        # Commit composer
        commit_panel = QWidget()
        commit_panel.setStyleSheet("QWidget { border-top: 1px solid #45475a; }")
        commit_layout = QVBoxLayout(commit_panel)
        commit_layout.setContentsMargins(10, 8, 10, 8)
        commit_layout.setSpacing(6)

        commit_header = QHBoxLayout()
        commit_title = QLabel("Commit")
        commit_title.setFont(ui_font(11, bold=True))
        commit_header.addWidget(commit_title)

        self._author_label = QLabel()
        self._author_label.setProperty("dim", True)
        self._author_label.setFont(ui_font(9))
        commit_header.addWidget(self._author_label)
        commit_header.addStretch()
        commit_layout.addLayout(commit_header)

        self._summary_input = QLineEdit()
        self._summary_input.setPlaceholderText("Commit summary (required, max 72 characters)")
        self._summary_input.setMaxLength(72)
        self._summary_input.textChanged.connect(self._on_commit_message_changed)
        commit_layout.addWidget(self._summary_input)

        self._body_input = QPlainTextEdit()
        self._body_input.setPlaceholderText("Extended description (optional)")
        self._body_input.setMaximumHeight(80)
        self._body_input.textChanged.connect(self._on_commit_message_changed)
        commit_layout.addWidget(self._body_input)

        self._validation_label = QLabel()
        self._validation_label.setStyleSheet(f"color: {ERROR.name()}; font-size: 9pt;")
        self._validation_label.setVisible(False)
        commit_layout.addWidget(self._validation_label)

        commit_buttons = QHBoxLayout()
        commit_buttons.setSpacing(8)

        self._signoff_btn = QPushButton("Sign Off")
        self._signoff_btn.setCheckable(True)
        self._signoff_btn.setFixedHeight(28)
        commit_buttons.addWidget(self._signoff_btn)

        self._amend_btn = QPushButton("Amend")
        self._amend_btn.setCheckable(True)
        self._amend_preview = None
        self._amend_draft = None
        self._amend_btn.toggled.connect(self._on_amend_toggled)
        self._amend_btn.setFixedHeight(28)
        commit_buttons.addWidget(self._amend_btn)

        commit_buttons.addStretch()

        self._commit_btn = QPushButton("  Commit")
        self._commit_btn.setProperty("accent", True)
        self._commit_btn.setFixedHeight(32)
        self._commit_btn.clicked.connect(self._do_commit)
        commit_buttons.addWidget(self._commit_btn)

        commit_layout.addLayout(commit_buttons)
        layout.addWidget(commit_panel)

        self._staged_list.itemSelectionChanged.connect(self._sync_file_actions)
        self._unstaged_list.itemSelectionChanged.connect(self._sync_file_actions)
        self._update_empty_state()

    def set_jobs(self, jobs: SessionJobs) -> None:
        self._jobs = jobs
        jobs.busy_changed.connect(self._set_busy)

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self._main_splitter,
            self._summary_input,
            self._body_input,
            self._signoff_btn,
            self._amend_btn,
            self._commit_btn,
            self._conflict_banner,
            self._refresh_btn,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self._sync_from_state()

    def _run_model_action(self, label, action, *, commit=False) -> None:
        if self._view_model is None or self._jobs.busy:
            return
        vm = self._view_model
        worker = vm.fork()
        self._status_label.setText(f"{label}…")

        def run():
            result = action(worker)
            return worker, result

        def apply(value):
            model, result = value
            vm.adopt(model, keep_draft=not (commit and result.success))
            self._sync_from_state()
            self.action_message.emit(result.message)
            if not result.success:
                QMessageBox.warning(self, f"{label} Failed", result.message)
            elif commit:
                self._summary_input.clear()
                self._body_input.clear()
                self._amend_draft = None
                self._amend_btn.setChecked(False)
                self.commit_succeeded.emit()
            else:
                self.working_tree_changed.emit()

        def error(value):
            QMessageBox.warning(self, f"{label} Failed", str(value))
            self.refresh_requested.emit()

        self._jobs.write(run, apply, error)

    def set_view_model(self, vm: WorkingTreeViewModel) -> None:
        if vm is not self._view_model:
            self._amend_draft = None
            self._amend_preview = None
            self._amend_btn.blockSignals(True)
            self._amend_btn.setChecked(False)
            self._amend_btn.blockSignals(False)
            self._summary_input.blockSignals(True)
            self._body_input.blockSignals(True)
            self._summary_input.setMaxLength(72)
            self._summary_input.setText(vm.state.commit_summary)
            self._body_input.setPlainText(vm.state.commit_body)
            self._summary_input.blockSignals(False)
            self._body_input.blockSignals(False)
            self._last_staged_signature = None
            self._last_unstaged_signature = None
        self._view_model = vm
        self._sync_from_state()

    def refresh_from_state(self) -> None:
        self._sync_from_state()

    def _sync_from_state(self) -> None:
        if self._view_model is None:
            self._update_empty_state()
            return

        state = self._view_model.state
        self._status_label.setText(
            self._status_label.fontMetrics().elidedText(
                state.message, Qt.TextElideMode.ElideMiddle, 400
            )
        )
        self._status_label.setToolTip(state.message)

        # Conflict banner
        if state.conflict_banner:
            self._conflict_banner.setVisible(True)
            self._conflict_text.setText(state.conflict_banner)
            self._conflict_text.setToolTip(state.conflict_recovery_message or "")
        else:
            self._conflict_banner.setVisible(False)

        self._continue_btn.setVisible(state.conflict_operation is not None)
        self._continue_btn.setEnabled(not state.has_conflicts)
        self._skip_btn.setVisible(state.conflict_operation in {"rebase", "cherry-pick", "revert"})
        self._abort_btn.setVisible(state.conflict_operation is not None)
        self._merge_tool_btn.setVisible(state.has_conflicts)

        # Author
        if state.commit_author.name or state.commit_author.email:
            self._author_label.setText(f"{state.commit_author.name} <{state.commit_author.email}>")
        else:
            self._author_label.setText("")

        # File lists
        self._populate_file_lists(state)

        # Preserve the selected side in both the header and actions.
        if state.selected_file:
            side = "Staged" if state.selected_staged else "Unstaged"
            title = f"{state.selected_file} — {side}"
            old_title = getattr(self, "_displayed_diff_title", None)
            scroll = self._diff_view._text.verticalScrollBar().value()
            self._diff_view.set_diff(
                title,
                state.selected_diff,
                state.selected_preview.message,
            )
            if title == old_title:
                self._diff_view._text.verticalScrollBar().setValue(scroll)
            self._displayed_diff_title = title
        else:
            self._diff_view.clear()
        picker = self._diff_toolbar._hunk_picker
        picker.blockSignals(True)
        picker.clear()
        for index, hunk in enumerate(state.selected_diff):
            picker.addItem(f"Hunk {index + 1}: {hunk.header}")
        picker.blockSignals(False)
        self._sync_diff_actions()

        self._sync_file_actions()
        self._stage_all_btn.setEnabled(any(e.unstaged for e in state.entries))
        self._unstage_all_btn.setEnabled(any(e.staged for e in state.entries))
        self._commit_btn.setEnabled(
            state.commit_enabled
            and (not self._amend_btn.isChecked() or self._amend_preview is not None)
        )

        if state.commit_validation_errors:
            self._validation_label.setText(" ".join(state.commit_validation_errors))
            self._validation_label.setVisible(True)
        else:
            self._validation_label.setVisible(False)

    def _populate_file_lists(self, state: WorkingTreeStateModel) -> None:
        search = self._search_text.lower()
        staged = [
            e
            for e in state.visible_entries
            if e.staged and (not search or search in e.path.lower())
        ]
        unstaged = [
            e
            for e in state.visible_entries
            if e.unstaged and (not search or search in e.path.lower())
        ]

        self._staged_label.setText(f"  Staged ({len(staged)})")
        self._unstaged_label.setText(f"  Unstaged ({len(unstaged)})")

        self._last_staged_signature = self._apply_entries_to_list(
            self._staged_list,
            staged,
            self._last_staged_signature,
        )
        self._last_unstaged_signature = self._apply_entries_to_list(
            self._unstaged_list,
            unstaged,
            self._last_unstaged_signature,
        )

    def _apply_entries_to_list(
        self,
        list_widget: QListWidget,
        entries: list[WorkingTreeEntry],
        previous_signature: tuple[tuple[str, str], ...] | None,
    ) -> tuple[tuple[str, str], ...]:
        signature = tuple((entry.path, entry.state.value) for entry in entries)
        if signature == previous_signature:
            return signature

        current_path = self._selected_path(list_widget)
        selected_paths = {
            item.data(Qt.ItemDataRole.UserRole) for item in list_widget.selectedItems()
        }
        scroll = list_widget.verticalScrollBar().value()
        list_widget.blockSignals(True)
        try:
            list_widget.clear()
            for entry in entries:
                self._add_file_item(list_widget, entry)
            self._restore_selection(list_widget, current_path)
            for index in range(list_widget.count()):
                item = list_widget.item(index)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) in selected_paths)
            list_widget.verticalScrollBar().setValue(scroll)
        finally:
            list_widget.blockSignals(False)
        return signature

    def _add_file_item(self, list_widget: QListWidget, entry: WorkingTreeEntry) -> None:
        icon_text, color = _STATE_ICONS.get(entry.state, ("?", FG_DIM))
        item = QListWidgetItem(f" {icon_text}  {entry.path}")
        item.setData(Qt.ItemDataRole.UserRole, entry.path)
        item.setForeground(color)
        list_widget.addItem(item)

    @staticmethod
    def _selected_path(list_widget: QListWidget) -> str | None:
        current = list_widget.currentItem()
        return current.data(Qt.ItemDataRole.UserRole) if current else None

    @staticmethod
    def _restore_selection(list_widget: QListWidget, path: str | None) -> None:
        if path is None:
            return
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == path:
                list_widget.setCurrentItem(item)
                return

    def _on_file_selected(self, item: QListWidgetItem | None) -> None:
        if item is None or self._view_model is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            # Deselect the other list
            sender_list = item.listWidget()
            if sender_list is self._staged_list:
                self._unstaged_list.blockSignals(True)
                self._unstaged_list.setCurrentItem(None)
                self._unstaged_list.clearSelection()
                self._unstaged_list.blockSignals(False)
            else:
                self._staged_list.blockSignals(True)
                self._staged_list.setCurrentItem(None)
                self._staged_list.clearSelection()
                self._staged_list.blockSignals(False)

            vm = self._view_model
            staged = sender_list is self._staged_list
            # Record selection intent immediately so an observer refresh cannot
            # supersede a pending diff with the previously selected file.
            vm.state.selected_file = path
            vm.state.selected_staged = staged
            vm.state.selected_diff = []
            from gitqt.services.status import DiffPreview

            vm.state.selected_preview = DiffPreview([])
            worker = vm.fork()
            self._diff_toolbar.setEnabled(False)
            self._diff_view.set_diff(path, [], "Loading diff…")

            def run():
                worker.select_file(path, staged=staged)
                return worker

            def apply(model):
                vm.adopt(model)
                self._sync_from_state()

            self._jobs.read(
                "working_tree",
                run,
                apply,
                lambda value: self._diff_view.set_diff(path, [], str(value)),
            )

    def _on_search_changed(self, text: str) -> None:
        if text == self._search_text:
            self._pending_search_text = None
            self._search_debounce.stop()
            return
        self._pending_search_text = text
        self._search_debounce.start()

    def _commit_search_change(self) -> None:
        if self._pending_search_text is None:
            return
        new_text = self._pending_search_text
        self._pending_search_text = None
        if new_text == self._search_text:
            return
        self._search_text = new_text
        self._sync_from_state()

    def _on_filter_toggled(self) -> None:
        if self._view_model is None:
            return
        active = set()
        for state, btn in self._filter_buttons.items():
            if btn.isChecked():
                active.add(state)
        if active:
            self._view_model.set_filters(active)
            self._sync_from_state()

    def _sync_file_actions(self) -> None:
        self._stage_btn.setEnabled(bool(self._unstaged_list.selectedItems()))
        self._unstage_btn.setEnabled(bool(self._staged_list.selectedItems()))

    def _stage_selected(self) -> None:
        self._change_files(stage=True)

    def _unstage_selected(self) -> None:
        self._change_files(stage=False)

    def _change_files(self, *, stage: bool, all_files: bool = False) -> None:
        if self._view_model is None:
            return
        if all_files:
            paths = [
                e.path
                for e in self._view_model.state.entries
                if (e.unstaged if stage else e.staged)
            ]
        else:
            widget = self._unstaged_list if stage else self._staged_list
            paths = [item.data(Qt.ItemDataRole.UserRole) for item in widget.selectedItems()]
        self._run_model_action(
            "Staging" if stage else "Unstaging",
            lambda model: model.change_files(paths, stage=stage),
        )

    def _sync_diff_actions(self) -> None:
        if self._view_model is None:
            self._diff_toolbar.setEnabled(False)
            return
        state = self._view_model.state
        self._diff_toolbar.setEnabled(state.selected_file is not None)
        selected = self._diff_view.selected_changes()
        hunk = self._diff_view.current_hunk() if selected is None else selected[0]
        picker = self._diff_toolbar._hunk_picker
        picker.blockSignals(True)
        picker.setCurrentIndex(hunk)
        picker.blockSignals(False)
        valid = (
            state.selected_preview.partial_supported
            and not state.has_conflicts
            and 0 <= hunk < len(state.selected_diff)
        )
        verb = "Unstage" if state.selected_staged else "Stage"
        target = "Selected Lines" if selected is not None else "Hunk"
        self._diff_toolbar._stage_btn.setText(f"{verb} {target}")
        self._diff_toolbar._discard_btn.setText(f"Discard {target}")
        self._diff_toolbar._stage_btn.setEnabled(valid)
        self._diff_toolbar._discard_btn.setEnabled(valid and not state.selected_staged)
        hint = (
            state.selected_preview.message
            or "Select changed lines within one hunk, or choose a hunk."
        )
        self._diff_toolbar._stage_btn.setToolTip(hint)
        self._diff_toolbar._discard_btn.setToolTip(
            "Discard only unstaged changes; a local backup is saved before modifying the file."
        )

    def _stage_hunk(self) -> None:
        if self._view_model is not None:
            self._change_hunk("unstage" if self._view_model.state.selected_staged else "stage")

    def _discard_hunk(self) -> None:
        self._change_hunk("discard")

    def _change_hunk(self, action: str) -> None:
        if self._view_model is None:
            return
        selected = self._diff_view.selected_changes()
        hunk_index = self._diff_view.current_hunk() if selected is None else selected[0]
        lines = None if selected is None else selected[1]
        if hunk_index < 0:
            return
        if action == "discard" and not self._confirm_discard(hunk_index, lines):
            return
        self._run_model_action(
            action.title(),
            lambda model: model.change_hunk(hunk_index, action=action, selected_lines=lines),
        )

    def _confirm_discard(self, hunk_index: int, lines: set[int] | None) -> bool:
        state = self._view_model.state
        hunk = state.selected_diff[hunk_index]
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Discard Unstaged Changes")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(
            f"Discard {'selected lines' if lines is not None else 'this hunk'} "
            f"in {state.selected_file}?"
        )
        dialog.setInformativeText(
            "Only the selected unstaged changes will be removed. Staged changes are preserved. "
            "A backup of the original file will be saved locally; its location is shown afterward."
        )
        prefixes = {"addition": "+", "deletion": "-", "context": " ", "meta": ""}
        dialog.setDetailedText(
            hunk.header
            + "\n"
            + "\n".join(
                prefixes[line.type.value] + line.text
                for index, line in enumerate(hunk.lines)
                if lines is None or index in lines
            )
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return dialog.exec() == QMessageBox.StandardButton.Discard

    def _operation_action(self, action: str) -> None:
        if self._view_model is not None and self._view_model.state.conflict_operation:
            self.operation_action_requested.emit(self._view_model.state.conflict_operation, action)

    def _on_commit_message_changed(self) -> None:
        if self._view_model is None:
            return
        self._view_model.set_commit_message(
            self._summary_input.text(),
            self._body_input.toPlainText(),
        )
        if self._view_model.state.commit_validation_errors:
            self._validation_label.setText(
                " ".join(self._view_model.state.commit_validation_errors)
            )
            self._validation_label.setVisible(True)
        else:
            self._validation_label.setVisible(False)

    def _on_amend_toggled(self, enabled):
        self._jobs.invalidate_channel("amend")
        self._amend_preview = None
        self._summary_input.setMaxLength(10000 if enabled else 72)
        if not enabled:
            if self._amend_draft is not None:
                self._summary_input.setText(self._amend_draft[0])
                self._body_input.setPlainText(self._amend_draft[1])
            self._amend_draft = None
            self._sync_from_state()
            return
        vm = self._view_model
        if vm is None or vm._commit_service is None or vm.state.repository_path is None:
            self._amend_btn.setChecked(False)
            return
        draft = (self._summary_input.text(), self._body_input.toPlainText())
        self._amend_draft = draft
        self._commit_btn.setEnabled(False)
        path, service = vm.state.repository_path, vm._commit_service

        def apply(preview):
            self._amend_preview = preview
            if (self._summary_input.text(), self._body_input.toPlainText()) == draft:
                self._summary_input.setText(preview.summary)
                self._body_input.setPlainText(preview.body)
            self._status_label.setText(
                "Amending "
                + preview.oid[:12]
                + ". This rewrites history; remote status reflects the last fetch."
            )
            self._commit_btn.setEnabled(vm.state.commit_enabled)

        def failed(error):
            self._amend_btn.setChecked(False)
            QMessageBox.warning(self, "Cannot Amend", str(error))

        self._jobs.read("amend", lambda: service.prepare_amend(path), apply, failed)

    def _do_commit(self) -> None:
        if self._view_model is None:
            return
        self._view_model.set_commit_message(
            self._summary_input.text(),
            self._body_input.toPlainText(),
        )
        amend, signoff = self._amend_btn.isChecked(), self._signoff_btn.isChecked()
        expected = None
        if amend:
            preview = self._amend_preview
            if preview is None:
                return
            expected = preview.oid
            shared = (
                (
                    "Known remote branches containing this commit: "
                    + ", ".join(preview.published_refs)
                )
                if preview.published_refs
                else (
                    "No cached remote branch contains this commit. This does not establish that it"
                    " was never shared."
                )
            )
            if (
                QMessageBox.question(
                    self,
                    "Amend Commit",
                    f"Rewrite {expected[:12]} with this message and staged"
                    f" changes?\n\n{shared}\nRemote information reflects the last fetch. Coordinate"
                    " any published-history rewrite. The original commit is retained in Recovery.",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        self._run_model_action(
            "Amend" if amend else "Commit",
            lambda model: model.commit_changes(
                amend=amend, signoff=signoff, expected_head=expected
            ),
            commit=True,
        )

    def _update_empty_state(self) -> None:
        self._diff_toolbar.setEnabled(False)
        self._status_label.setText("Open a repository to view file changes.")
        self._staged_list.clear()
        self._unstaged_list.clear()
        self._diff_view.clear()
