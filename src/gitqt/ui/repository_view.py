"""Repository workspace view assembling sidebar, content tabs, and operation log."""

from __future__ import annotations

from copy import copy, deepcopy

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gitqt.app.bootstrap import AppServices
from gitqt.app.references import ReferencesState, ReferencesViewModel
from gitqt.app.remote_operations import RemoteOperationsState, RemoteOperationsViewModel
from gitqt.app.working_tree import WorkingTreeViewModel
from gitqt.services.branch import BranchSnapshot
from gitqt.services.observer import repository_fingerprint
from gitqt.services.process_context import redact
from gitqt.services.recovery import RecoveryService
from gitqt.services.session_jobs import SessionJobs
from gitqt.services.settings import AppSettings
from gitqt.ui.avatars import render_avatar
from gitqt.ui.dialogs import (
    ConfirmationDialog,
    CreateBranchDialog,
    CreateStashDialog,
    CreateTagDialog,
)
from gitqt.ui.history_view import HistoryView
from gitqt.ui.operation_log import OperationLogPanel
from gitqt.ui.settings_dialog import SettingsDialog
from gitqt.ui.sidebar import Sidebar
from gitqt.ui.theme import ACCENT, FG_SECONDARY, set_ui_font_family, ui_font
from gitqt.ui.working_tree_view import WorkingTreeView


class RepositoryView(QWidget):
    """Full repository workspace: sidebar + content tabs + operation log."""

    back_to_dashboard = Signal()

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._jobs = SessionJobs(services.worker_pool, parent=self)
        self._branch_snapshot = None
        self._repository_path: str | None = None
        self._remote_operation_busy = False
        self._operation_action_busy = False

        # View models (created per-repository load)
        self._working_tree_vm: WorkingTreeViewModel | None = None
        self._references_vm: ReferencesViewModel | None = None
        self._remote_ops_vm: RemoteOperationsViewModel | None = None

        self._build_ui()
        self._connect_signals()
        self._working_tree.set_jobs(self._jobs)
        self._jobs.busy_changed.connect(self._set_busy)
        self._jobs.progress.connect(self._log_info)
        self._fingerprint = None
        self._observing = False
        self._observe_timer = QTimer(self)
        self._observe_timer.setInterval(2000)
        self._observe_timer.timeout.connect(self._observe_repository)
        self._observe_timer.start()
        QApplication.instance().applicationStateChanged.connect(self._on_application_state_changed)

    @Slot(Qt.ApplicationState)
    def _on_application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self._observe_repository()

    def load_repository(self, repository_path: str) -> bool:
        if not self._jobs.activate(repository_path):
            return False
        same_repo = repository_path == self._repository_path
        self._observing = False
        self._repository_path = repository_path
        self._invalidate_all_caches()
        if not same_repo:
            self._create_view_models()
            self._branch_snapshot = None
            self._fingerprint = None
            self._observing = False
            self._branch_label.setText("Loading…")
            self._tracking_label.clear()
            self._user_chip.setVisible(False)
            self._op_log.clear()
            self._history_view.clear()
            self._sidebar.clear_all()
            self._sidebar.set_stashes([])
            self._sidebar.set_tags([])
            self._sidebar.set_remotes([])
            if self._working_tree_vm is not None:
                self._working_tree.set_view_model(self._working_tree_vm)
        self._refresh_all()
        return True

    def _observe_repository(self) -> None:
        path, runner = self._repository_path, self._services.git_runner
        if (
            self._observing
            or not self.isVisible()
            or self._jobs.busy
            or path is None
            or runner is None
        ):
            return

        self._observing = True

        def apply(fingerprint):
            self._observing = False
            changed = fingerprint != self._fingerprint
            self._fingerprint = fingerprint
            if changed:
                self._invalidate_all_caches()
                self._refresh_all()

        def failed(value):
            self._observing = False
            self._log_error(f"Refresh failed: {value}")

        self._jobs.read(
            "observer",
            lambda: repository_fingerprint(runner, path),
            apply,
            failed,
        )

    def _set_busy(self, busy: bool) -> None:
        self._cancel_btn.setVisible(busy and self._jobs.cancellable)
        for widget in (self._sidebar, self._history_view, self._back_btn, self._settings_btn):
            widget.setEnabled(not busy)
        self._set_remote_buttons_enabled(not busy)
        if busy:
            self._op_log.expand()
            self._observing = False
            self._history_view.invalidate_jobs()

    def _run_command(self, label, run, apply=None, *, cancellable=False) -> None:
        if self._jobs.busy:
            return
        self._jobs.pool = self._services.worker_pool
        self._log_info(f"{label} in progress…")

        def completed(result):
            if apply is not None:
                apply(result)
            if getattr(result, "success", True):
                self._log_info(getattr(result, "message", f"{label} completed."))
            else:
                message = getattr(result, "message", f"{label} failed.")
                self._log_error(message)
                QMessageBox.warning(self, f"{label} Failed", message)

        def failed(value):
            self._log_error(str(value))
            QMessageBox.warning(self, f"{label} Failed", str(value))

        def finished():
            self._remote_operation_busy = False
            self._operation_action_busy = False
            self._invalidate_all_caches()
            self._refresh_all()

        self._jobs.write(run, completed, failed, finished, cancellable=cancellable)

    def _invalidate_all_caches(self) -> None:
        """Drop cached snapshots so the next refresh reflects fresh git state."""
        if self._repository_path is None:
            return
        path = self._repository_path
        s = self._services
        for service in (s.status, s.branch, s.history, s.remote, s.stash, s.tag):
            if service is not None and hasattr(service, "invalidate_repository"):
                service.invalidate_repository(path)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setStyleSheet("QWidget { background: #313244; border-bottom: 1px solid #45475a; }")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 6, 12, 6)

        self._back_btn = QPushButton("← Dashboard")
        self._back_btn.setFlat(True)
        self._back_btn.setStyleSheet(
            "QPushButton { color: #89b4fa; border: none; font-weight: 500; padding: 4px 8px; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self._back_btn.clicked.connect(self.back_to_dashboard)
        header_layout.addWidget(self._back_btn)

        self._repo_name_label = QLabel("Repository")
        self._repo_name_label.setFont(ui_font(13, bold=True))
        header_layout.addWidget(self._repo_name_label)

        header_layout.addStretch()

        self._branch_label = QLabel()
        self._branch_label.setStyleSheet(
            f"color: {ACCENT.name()}; background: rgba(137,180,250,20); "
            "border-radius: 4px; padding: 3px 10px; font-weight: 600;"
        )
        header_layout.addWidget(self._branch_label)

        self._tracking_label = QLabel()
        self._tracking_label.setProperty("dim", True)
        header_layout.addWidget(self._tracking_label)

        sep = QLabel("  |  ")
        sep.setProperty("dim", True)
        header_layout.addWidget(sep)

        self._fetch_btn = QPushButton("Fetch")
        self._fetch_btn.setFixedHeight(28)
        self._fetch_btn.clicked.connect(lambda: self._configure_transfer("fetch"))
        header_layout.addWidget(self._fetch_btn)

        self._pull_btn = QPushButton("Pull")
        self._pull_btn.setFixedHeight(28)
        self._pull_btn.clicked.connect(lambda: self._configure_transfer("pull"))
        header_layout.addWidget(self._pull_btn)

        self._push_btn = QPushButton("Push")
        self._push_btn.setFixedHeight(28)
        self._push_btn.clicked.connect(lambda: self._configure_transfer("push"))
        header_layout.addWidget(self._push_btn)

        self._settings_btn = QToolButton()
        self._settings_btn.setText("\u2699")
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.setAutoRaise(True)
        self._settings_btn.setFixedSize(30, 28)
        self._settings_btn.setStyleSheet(
            "QToolButton { color: #cdd6f4; font-size: 16pt; border: 1px solid #45475a;"
            "              border-radius: 6px; padding: 0 4px; }"
            "QToolButton:hover { color: #89b4fa; border-color: #89b4fa; }"
        )
        self._settings_btn.clicked.connect(self._open_settings_dialog)
        header_layout.addWidget(self._settings_btn)

        self._user_chip = self._build_user_chip()
        header_layout.addWidget(self._user_chip)

        root.addWidget(header)

        # Main body: sidebar | content
        body_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._sidebar = Sidebar(
            split_branch_paths=self._services.settings.split_branch_paths,
        )
        body_splitter.addWidget(self._sidebar)

        # Center: tab widget
        self._tabs = QTabWidget()
        self._working_tree = WorkingTreeView()
        self._history_view = HistoryView()
        self._history_view.set_row_alpha(self._services.settings.history_row_alpha)
        self._tabs.addTab(self._working_tree, "Working Tree")
        self._tabs.addTab(self._history_view, "History")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        body_splitter.addWidget(self._tabs)

        body_splitter.setStretchFactor(0, 0)
        body_splitter.setStretchFactor(1, 1)
        body_splitter.setSizes([220, 800])

        root.addWidget(body_splitter, stretch=1)

        self._cancel_btn = QPushButton("Cancel Transfer")
        self._cancel_btn.clicked.connect(self._jobs.cancel)
        self._cancel_btn.hide()
        root.addWidget(self._cancel_btn)

        # Operation log
        self._op_log = OperationLogPanel()
        root.addWidget(self._op_log)

    def _connect_signals(self) -> None:
        self._sidebar.manage_ref_requested.connect(
            lambda kind, ref: self._open_ref_manager((kind, ref))
        )
        self._working_tree.refresh_requested.connect(self._force_refresh_working_tree)
        self._working_tree.external_diff_requested.connect(self._launch_external_diff)
        self._working_tree.external_merge_requested.connect(self._launch_external_merge)
        self._working_tree.commit_succeeded.connect(self._on_commit_succeeded)
        self._working_tree.working_tree_changed.connect(self._on_working_tree_changed)
        self._working_tree.operation_action_requested.connect(self._resolve_operation)
        self._working_tree.action_message.connect(self._log_info)

        self._history_view.checkout_commit_requested.connect(self._checkout_commit)
        self._history_view.create_branch_from_commit_requested.connect(
            self._create_branch_from_commit
        )
        self._history_view.create_tag_at_commit_requested.connect(self._create_tag_at_commit)
        self._history_view.cherry_pick_commit_requested.connect(self._cherry_pick_commit)
        self._history_view.revert_commit_requested.connect(self._revert_commit)
        self._history_view.merge_commit_requested.connect(self._merge_commit_into_head)
        self._history_view.rebase_onto_commit_requested.connect(self._rebase_onto_commit)
        self._history_view.reset_to_commit_requested.connect(self._reset_to_commit)

        self._sidebar.branch_checkout_requested.connect(self._checkout_branch)
        self._sidebar.branch_create_requested.connect(self._show_create_branch_dialog)
        self._sidebar.branch_delete_requested.connect(self._show_delete_branch_dialog)
        self._sidebar.stash_apply_requested.connect(self._apply_stash)
        self._sidebar.stash_pop_requested.connect(self._pop_stash)
        self._sidebar.stash_drop_requested.connect(self._drop_stash)
        self._sidebar.stash_create_requested.connect(self._show_create_stash_dialog)
        self._sidebar.tag_create_requested.connect(self._show_create_tag_dialog)
        self._sidebar.remote_fetch_requested.connect(self._fetch_remote)
        self._sidebar.remote_push_requested.connect(self._push_remote)

    def _create_view_models(self) -> None:
        s = self._services
        if s.status:
            self._working_tree_vm = WorkingTreeViewModel(
                status_service=s.status,
                commit_service=s.commit,
                conflict_service=s.conflict,
                external_tool_service=s.external_tools,
                external_diff_tool_path=s.settings.external_diff_tool,
                external_merge_tool_path=s.settings.external_merge_tool,
            )
        if s.stash and s.tag:
            self._references_vm = ReferencesViewModel(s.stash, s.tag)
        if s.remote:
            self._remote_ops_vm = RemoteOperationsViewModel(s.remote)

    def _submit_read(
        self,
        *,
        key: str,
        run,
        on_result,
        error_label: str | None = None,
    ) -> None:
        """Submit a read-only job to the worker pool with error logging."""

        self._jobs.pool = self._services.worker_pool
        path = self._repository_path
        services = (
            self._services.status,
            self._services.branch,
            self._services.history,
            self._services.remote,
            self._services.stash,
            self._services.tag,
        )

        def fresh_run():
            for service in services:
                if service is not None and path is not None:
                    service.invalidate_repository(path)
            return run()

        self._jobs.read(
            key.split("::", 1)[0],
            fresh_run,
            on_result,
            lambda value: self._log_error(f"{error_label or key} failed: {value}"),
        )

    def _refresh_all(self) -> None:
        if self._repository_path is None or self._jobs.busy:
            return

        from pathlib import Path

        repo_name = Path(self._repository_path).name or self._repository_path
        self._repo_name_label.setText(repo_name)

        self._refresh_branch_header()
        self._refresh_working_tree()
        self._refresh_sidebar_branches()
        self._refresh_references()
        self._refresh_remote_ops()
        self._refresh_user_chip()

        if self._tabs.currentIndex() == 1:
            self._refresh_history()

    def _refresh_branch_header(self) -> None:
        if self._repository_path is None or self._services.branch is None:
            self._apply_branch_header(None)
            return
        branch_service = self._services.branch
        path = self._repository_path

        def _run() -> BranchSnapshot | None:
            try:
                return branch_service.get_snapshot(path)
            except Exception:
                return None

        self._submit_read(
            key=f"branch_header::{path}",
            run=_run,
            on_result=self._apply_branch_header,
            error_label="Branch header refresh",
        )

    def _apply_branch_header(self, snapshot: BranchSnapshot | None) -> None:
        self._branch_snapshot = snapshot
        if snapshot is None:
            self._branch_label.setText("unknown")
            self._tracking_label.setText("")
            return

        tracking = snapshot.tracking
        if tracking.is_detached_head:
            hash_text = tracking.head_commit_short_hash or "???"
            self._branch_label.setText(f"DETACHED @ {hash_text}")
        elif tracking.branch_name:
            self._branch_label.setText(tracking.branch_name)
        else:
            self._branch_label.setText("(no branch)")

        parts = []
        if tracking.ahead:
            parts.append(f"↑{tracking.ahead}")
        if tracking.behind:
            parts.append(f"↓{tracking.behind}")
        self._tracking_label.setText(" ".join(parts))

    def _force_refresh_working_tree(self) -> None:
        if self._services.status is not None and self._repository_path is not None:
            self._services.status.invalidate_repository(self._repository_path)
        self._refresh_working_tree()

    def _refresh_working_tree(self) -> None:
        if self._repository_path is None or self._working_tree_vm is None:
            return
        vm = self._working_tree_vm
        worker = vm.fork()
        path = self._repository_path

        def run():
            worker.load_repository(path)
            return worker

        def apply(model):
            vm.adopt(model)
            self._working_tree.set_view_model(vm)

        self._submit_read(
            key="working_tree", run=run, on_result=apply, error_label="Working tree refresh"
        )

    def _refresh_sidebar_branches(self) -> None:
        if self._repository_path is None or self._services.branch is None:
            return
        branch_service = self._services.branch
        path = self._repository_path

        def _run() -> BranchSnapshot | None:
            try:
                return branch_service.get_snapshot(path)
            except Exception:
                return None

        def _apply(snapshot: BranchSnapshot | None) -> None:
            if snapshot is not None:
                self._sidebar.set_branch_snapshot(snapshot)

        self._submit_read(
            key=f"branch_sidebar::{path}",
            run=_run,
            on_result=_apply,
            error_label="Sidebar branch refresh",
        )

    def _refresh_references(self) -> None:
        if self._repository_path is None or self._references_vm is None:
            return
        target = self._references_vm
        vm = copy(target)
        vm.state = deepcopy(target.state)
        path = self._repository_path

        def _run() -> ReferencesState:
            vm.load_repository(path)
            return vm.state

        def _apply(state: ReferencesState) -> None:
            target.state = state
            self._sidebar.set_stashes(state.stashes)
            self._sidebar.set_tags(state.tags)

        self._submit_read(
            key=f"references::{path}",
            run=_run,
            on_result=_apply,
            error_label="References refresh",
        )

    def _refresh_remote_ops(self) -> None:
        if self._repository_path is None or self._remote_ops_vm is None:
            return
        target = self._remote_ops_vm
        vm = copy(target)
        vm.state = deepcopy(target.state)
        path = self._repository_path

        def _run() -> RemoteOperationsState:
            vm.load_repository(path)
            return vm.state

        def _apply(state: RemoteOperationsState) -> None:
            selected = target.state.selected_remote
            if selected in {remote.name for remote in state.remotes}:
                state.selected_remote = selected
            target.state = state
            self._sidebar.set_remotes(state.remotes)
            remote_names = [remote.name for remote in state.remotes]
            self._history_view.set_known_remotes(remote_names)

        self._submit_read(
            key=f"remote_ops::{path}",
            run=_run,
            on_result=_apply,
            error_label="Remote operations refresh",
        )

    def _refresh_history(self) -> None:
        if self._repository_path is None:
            return
        self._history_view.set_history_service(self._services.history)
        if hasattr(self._history_view, "set_worker_pool"):
            self._history_view.set_worker_pool(self._services.worker_pool)
        self._history_view.load_repository(self._repository_path)

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._refresh_history()

    def _on_commit_succeeded(self) -> None:
        self._invalidate_all_caches()
        self._refresh_all()

    def _on_working_tree_changed(self) -> None:
        if self._services.branch is not None and self._repository_path is not None:
            self._services.branch.invalidate_repository(self._repository_path)

    # -----------------------------------------------------------------------
    # Branch actions
    # -----------------------------------------------------------------------

    def _checkout_branch(self, branch_name: str) -> None:
        if self._repository_path is None or self._services.branch is None:
            return
        path, service = self._repository_path, self._services.branch
        self._run_command("Checkout", lambda: service.checkout_branch(path, branch_name))

    def _checkout_commit(self, commit_hash: str) -> None:
        if self._repository_path is None or self._services.branch is None:
            return
        dialog = ConfirmationDialog(
            title="Checkout Commit",
            body=(
                f"Checkout commit {commit_hash}?\n\n"
                "This puts the working tree into a detached HEAD state. "
                "Create a branch first if you plan to make new commits."
            ),
            confirm_label="Checkout Commit",
            recovery_hint="Return to a branch with a sidebar checkout or 'git checkout <branch>'.",
            danger=False,
            parent=self,
        )
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.branch
        self._run_command("Checkout", lambda: service.checkout_branch(path, commit_hash))

    def _create_branch_from_commit(self, commit_hash: str) -> None:
        if self._repository_path is None or self._services.branch is None:
            return
        snapshot = self._branch_snapshot

        current = snapshot.tracking.branch_name if snapshot else None
        local_names = [b.name for b in snapshot.local_branches] if snapshot else []

        dialog = CreateBranchDialog(
            current_branch=current,
            local_branches=local_names,
            parent=self,
        )
        dialog.setWindowTitle(f"Create Branch at {commit_hash}")
        dialog._start_combo.setCurrentText(commit_hash)  # type: ignore[attr-defined]

        if not dialog.exec():
            return

        path, service = self._repository_path, self._services.branch
        name, start, checkout = (
            dialog.branch_name(),
            dialog.start_point() or commit_hash,
            dialog.checkout(),
        )
        self._run_command(
            "Create Branch",
            lambda: service.create_branch(path, name, start_point=start, checkout=checkout),
        )

    def _create_tag_at_commit(self, commit_hash: str) -> None:
        if self._references_vm is None or self._repository_path is None:
            return

        dialog = CreateTagDialog(current_branch=commit_hash, parent=self)
        dialog.setWindowTitle(f"Create Tag at {commit_hash}")
        dialog._target_input.setText(commit_hash)  # type: ignore[attr-defined]

        if not dialog.exec():
            return

        target = dialog.target() or commit_hash
        path, service = self._repository_path, self._services.tag
        name, target, annotation = (
            dialog.tag_name(),
            dialog.target() or None,
            dialog.annotation() or None,
        )
        self._run_command(
            "Create Tag",
            lambda: service.create_tag(path, name, target=target, annotation=annotation),
        )

    def _resolve_operation(self, operation: str, action: str) -> None:
        service = self._services.commit_operations
        path = self._repository_path
        if (
            service is None
            or path is None
            or self._operation_action_busy
            or self._remote_operation_busy
        ):
            return
        if action in {"abort", "skip"}:
            impact = (
                "Abort the operation and ask Git to restore its starting state? "
                "Conflict-resolution edits made during the operation may be lost."
                if action == "abort"
                else (
                    "Skip the current commit? Its changes and current conflict resolutions "
                    "will not be included in the result of this operation."
                )
            )
            dialog = ConfirmationDialog(
                title=f"{action.title()} {operation.title()}",
                body=impact,
                confirm_label=action.title(),
                danger=True,
                parent=self,
            )
            if not dialog.exec():
                return
        self._run_command(
            f"{operation.title()} {action}",
            lambda: service.resolve_operation(path, operation, action),
        )

    def _cherry_pick_commit(self, commit_hash: str) -> None:
        if self._services.commit_operations is None or self._repository_path is None:
            return
        dialog = ConfirmationDialog(
            title="Cherry-pick Commit",
            body=(
                f"Cherry-pick commit {commit_hash} onto the current branch?\n\n"
                "Git will create a new commit on HEAD with the same changes."
            ),
            confirm_label="Cherry-pick",
            recovery_hint=(
                "If conflicts occur, resolve and stage the files, then use Continue "
                "or Abort in the Working Tree panel."
            ),
            danger=False,
            parent=self,
        )
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.commit_operations
        self._run_command("Cherry Pick", lambda: service.cherry_pick(path, commit_hash))

    def _revert_commit(self, commit_hash: str) -> None:
        if self._services.commit_operations is None or self._repository_path is None:
            return
        dialog = ConfirmationDialog(
            title="Revert Commit",
            body=(
                f"Revert commit {commit_hash}?\n\n"
                "Git will create a new commit that undoes the changes from this commit."
            ),
            confirm_label="Revert",
            recovery_hint=(
                "If conflicts occur, resolve and stage the files, then use Continue "
                "or Abort in the Working Tree panel."
            ),
            danger=False,
            parent=self,
        )
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.commit_operations
        self._run_command("Revert", lambda: service.revert(path, commit_hash))

    def _merge_commit_into_head(self, commit_hash: str) -> None:
        if self._services.commit_operations is None or self._repository_path is None:
            return
        dialog = ConfirmationDialog(
            title="Merge Commit",
            body=(
                f"Merge {commit_hash} into the current branch?\n\n"
                "A merge commit will be created (no fast-forward)."
            ),
            confirm_label="Merge",
            recovery_hint=(
                "If conflicts occur, use Continue or Abort in the Working Tree panel to stop."
            ),
            danger=False,
            parent=self,
        )
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.commit_operations
        self._run_command("Merge Commit", lambda: service.merge_commit(path, commit_hash))

    def _rebase_onto_commit(self, commit_hash: str) -> None:
        if self._services.commit_operations is None or self._repository_path is None:
            return
        dialog = ConfirmationDialog(
            title="Rebase Onto Commit",
            body=(
                f"Rebase the current branch onto {commit_hash}?\n\n"
                "This rewrites history on the current branch. Do not rebase commits "
                "that have already been pushed and shared."
            ),
            confirm_label="Rebase",
            recovery_hint=(
                "If conflicts occur, resolve and stage the files, then use Continue or "
                "Abort in the Working Tree panel."
            ),
            danger=True,
            parent=self,
        )
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.commit_operations
        self._run_command("Rebase Onto", lambda: service.rebase_onto(path, commit_hash))

    def _reset_to_commit(self, commit_hash: str, mode_id: str) -> None:
        if self._services.commit_operations is None or self._repository_path is None:
            return

        from gitqt.services.commit_operations import ResetMode

        try:
            mode = ResetMode(mode_id)
        except ValueError:
            return

        impact = {
            ResetMode.SOFT: (
                "Move HEAD to the commit. Index and working tree are preserved; "
                "changes will appear as staged."
            ),
            ResetMode.MIXED: (
                "Move HEAD to the commit and reset the index. Working tree changes "
                "are preserved as unstaged modifications."
            ),
            ResetMode.HARD: (
                "Move HEAD to the commit and discard index + working tree changes. "
                "Uncommitted work will be lost."
            ),
        }[mode]

        dialog = ConfirmationDialog(
            title=f"Reset ({mode.value.title()}) to Commit",
            body=f"Reset the current branch to {commit_hash} ({mode.value})?\n\n{impact}",
            confirm_label=f"Reset ({mode.value.title()})",
            recovery_hint=(
                "Recovery retains HEAD and tracked work before resetting. Hard reset requires no"
                " submodules, untracked, or ignored files. Submodule worktrees are not reset."
            ),
            danger=mode is ResetMode.HARD,
            parent=self,
        )
        if not dialog.exec():
            return

        path, service = self._repository_path, self._services.commit_operations

        def run():
            RecoveryService(self._services.git_runner).before_reset(
                path, hard=mode is ResetMode.HARD
            )
            return service.reset_to(path, commit_hash, mode)

        self._run_command("Reset To", run)

    def _apply_commit_op_result(self, result, op_label: str) -> None:
        if result.success:
            self._log_info(f"{op_label}: {result.message}")
        else:
            self._log_error(f"{op_label} failed: {result.message}")
            QMessageBox.warning(self, f"{op_label} Failed", result.message)
        self._invalidate_all_caches()
        self._refresh_all()

    def _show_create_branch_dialog(self) -> None:
        if self._repository_path is None or self._services.branch is None:
            return
        snapshot = self._branch_snapshot

        current = snapshot.tracking.branch_name if snapshot else None
        local_names = [b.name for b in snapshot.local_branches] if snapshot else []

        dialog = CreateBranchDialog(
            current_branch=current,
            local_branches=local_names,
            parent=self,
        )
        if not dialog.exec():
            return

        path, service = self._repository_path, self._services.branch
        name, start, checkout = (
            dialog.branch_name(),
            dialog.start_point() or None,
            dialog.checkout(),
        )
        self._run_command(
            "Create Branch",
            lambda: service.create_branch(path, name, start_point=start, checkout=checkout),
        )

    def _show_delete_branch_dialog(self, branch_name: str) -> None:
        if self._repository_path is None or self._services.branch is None:
            return

        dialog = ConfirmationDialog(
            title="Delete Branch",
            body=(
                f'Delete local branch "{branch_name}"?\n\n'
                "This removes the branch reference but not commits "
                "that are still reachable elsewhere."
            ),
            confirm_label="Delete Branch",
            recovery_hint="Recovery retains the branch tip so you can recreate a branch later.",
            parent=self,
        )
        if not dialog.exec():
            return

        path, service = self._repository_path, self._services.branch

        def run():
            RecoveryService(self._services.git_runner).retain(
                path, f"refs/heads/{branch_name}", "deleted-branch"
            )
            return service.delete_branch(path, branch_name)

        self._run_command("Delete Branch", run)

    def _apply_stash(self, stash_ref: str) -> None:
        if self._references_vm is None:
            return
        path, service = self._repository_path, self._services.stash
        self._run_command("Apply Stash", lambda: service.apply_stash(path, stash_ref))

    def _pop_stash(self, stash_ref: str) -> None:
        if self._references_vm is None:
            return
        dialog = ConfirmationDialog(
            title="Pop Stash",
            body=f"Pop {stash_ref}? Git will apply changes and remove this stash entry.",
            confirm_label="Pop Stash",
            recovery_hint="If pop fails, resolve conflicts and use git stash apply instead.",
            parent=self,
        )
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.stash

        def run():
            RecoveryService(self._services.git_runner).retain(
                path, stash_ref, "pop-stash", kind="stash"
            )
            return service.pop_stash(path, stash_ref)

        self._run_command("Pop Stash", run)

    def _drop_stash(self, stash_ref: str) -> None:
        if self._references_vm is None:
            return
        dialog = ConfirmationDialog(
            title="Drop Stash",
            body=f"Drop {stash_ref}? This removes it from the stash list.",
            confirm_label="Drop Stash",
            recovery_hint=(
                "Recovery retains this stash object until its recovery reference is manually"
                " removed."
            ),
            parent=self,
        )
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.stash

        def run():
            RecoveryService(self._services.git_runner).retain(
                path, stash_ref, "drop-stash", kind="stash"
            )
            return service.drop_stash(path, stash_ref)

        self._run_command("Drop Stash", run)

    def _show_create_stash_dialog(self) -> None:
        if self._references_vm is None or self._repository_path is None:
            return
        dialog = CreateStashDialog(parent=self)
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.stash
        message, untracked = dialog.stash_message() or None, dialog.include_untracked()
        self._run_command(
            "Create Stash",
            lambda: service.create_stash(path, message=message, include_untracked=untracked),
        )

    def _show_create_tag_dialog(self) -> None:
        if self._references_vm is None or self._repository_path is None:
            return
        current = self._branch_snapshot.tracking.branch_name if self._branch_snapshot else None

        dialog = CreateTagDialog(current_branch=current, parent=self)
        if not dialog.exec():
            return
        path, service = self._repository_path, self._services.tag
        name, target, annotation = (
            dialog.tag_name(),
            dialog.target() or None,
            dialog.annotation() or None,
        )
        self._run_command(
            "Create Tag",
            lambda: service.create_tag(path, name, target=target, annotation=annotation),
        )

    def _run_remote_op_async(
        self,
        *,
        op_name: str,
        failure_title: str,
        runner,
        remote_name: str | None = None,
    ) -> None:
        if self._remote_ops_vm is None or self._repository_path is None or self._jobs.busy:
            return
        target = self._remote_ops_vm
        vm = copy(target)
        vm.state = deepcopy(target.state)
        if remote_name is not None:
            vm.select_remote(remote_name)
        self._remote_operation_busy = True
        self._run_command(
            op_name,
            lambda: runner(vm),
            lambda _: setattr(target, "state", vm.state),
            cancellable=True,
        )

    def _set_remote_buttons_enabled(self, enabled: bool) -> None:
        for button in (self._fetch_btn, self._pull_btn, self._push_btn):
            button.setEnabled(enabled)

    def _do_fetch(self) -> None:
        self._run_remote_op_async(
            op_name="Fetch",
            failure_title="Fetch Failed",
            runner=lambda vm: vm.fetch_selected_remote(),
        )

    def _do_pull(self) -> None:
        self._run_remote_op_async(
            op_name="Pull",
            failure_title="Pull Failed",
            runner=lambda vm: vm.pull_selected_remote(),
        )

    def _do_push(self) -> None:
        self._run_remote_op_async(
            op_name="Push",
            failure_title="Push Failed",
            runner=lambda vm: vm.push_selected_remote(),
        )

    def _fetch_remote(self, remote_name: str) -> None:
        self._run_remote_op_async(
            op_name="Fetch",
            failure_title="Fetch Failed",
            runner=lambda vm: vm.fetch_selected_remote(),
            remote_name=remote_name,
        )

    def _push_remote(self, remote_name: str) -> None:
        self._configure_transfer("push", remote_name)

    def _configure_transfer(self, operation, selected=None):
        if self._jobs.busy or self._remote_ops_vm is None or self._services.remote is None:
            return
        from gitqt.ui.remote_dialogs import TransferDialog

        snapshot = self._branch_snapshot
        dialog = TransferDialog(
            operation,
            self._remote_ops_vm.state.remotes,
            snapshot.tracking if snapshot else None,
            selected,
            self,
        )
        if not dialog.exec():
            return
        remote, branch = dialog.remote.currentText(), dialog.branch.text().strip()
        path, service = self._repository_path, self._services.remote
        option, strategy, force = (
            dialog.option.isChecked(),
            dialog.strategy.currentIndex(),
            dialog.force.isChecked(),
        )
        if operation != "fetch" and (not branch or branch.startswith("-") or ":" in branch):
            QMessageBox.warning(self, "Invalid Branch", "Enter one destination branch name.")
            return
        if operation == "fetch":
            self._run_command(
                "Fetch", lambda: service.fetch(path, remote=remote, prune=option), cancellable=True
            )
        elif operation == "pull":
            self._run_command(
                "Pull",
                lambda: service.pull(
                    path, remote=remote, branch=branch, ff_only=strategy == 0, rebase=strategy == 2
                ),
                cancellable=True,
            )
        elif force:
            # Finish the read/retention job before opening the confirmation; a nested
            # write must not be started inside its completion cleanup.
            preview = {}

            def complete(value):
                preview["value"] = value

            def finished():
                self._invalidate_all_caches()
                self._refresh_all()
                if "value" not in preview:
                    return
                prepared = preview["value"]
                destination = redact(prepared.push_url)
                impact = prepared.message
                confirm = ConfirmationDialog(
                    title="Force Push with Lease",
                    body=(
                        f"Replace {remote}/{branch} at {destination} with commit"
                        f" {prepared.source_oid}?\n\n{impact}"
                    ),
                    confirm_label="Force Push with Lease",
                    recovery_hint=(
                        "If the remote tip changes after this preview, Git will reject the push."
                    ),
                    danger=True,
                    parent=self,
                )
                if confirm.exec():
                    self._run_command(
                        "Force Push",
                        lambda: service.push(
                            path,
                            remote=remote,
                            branch=f"{prepared.source_oid}:{branch}",
                            set_upstream=False,
                            force_with_lease=True,
                            expected_oid=prepared.expected_oid,
                            target_url=prepared.push_url,
                        ),
                        cancellable=True,
                    )

            self._jobs.write(
                lambda: service.force_preview(path, remote, branch),
                complete,
                lambda error: QMessageBox.warning(self, "Preview Failed", str(error)),
                finished,
                cancellable=True,
            )
        else:
            self._run_command(
                "Push",
                lambda: service.push(
                    path, remote=remote, branch=f"HEAD:{branch}", set_upstream=option
                ),
                cancellable=True,
            )

    def _open_ref_manager(self, selection=None):
        if self._jobs.busy or self._repository_path is None or self._services.git_runner is None:
            return
        from gitqt.ui.ref_manager import RefManagerDialog

        self._jobs.invalidate_reads()
        self._observing = False
        RefManagerDialog(self._services, self._repository_path, self, selection).exec()
        self._invalidate_all_caches()
        self._refresh_all()

    def _open_remotes(self):
        if self._jobs.busy or self._repository_path is None or self._services.remote is None:
            return
        from gitqt.ui.remote_dialogs import RemotesDialog

        self._jobs.invalidate_reads()
        self._observing = False
        RemotesDialog(self._services, self._repository_path, self).exec()
        self._invalidate_all_caches()
        self._refresh_all()

    def _open_recovery(self):
        if self._jobs.busy or self._repository_path is None or self._services.git_runner is None:
            return
        from gitqt.ui.recovery_dialog import RecoveryDialog

        self._jobs.invalidate_reads()
        self._observing = False
        RecoveryDialog(self._services, self._repository_path, self).exec()
        self._invalidate_all_caches()
        self._refresh_all()

    # -----------------------------------------------------------------------
    # Settings / user chip
    # -----------------------------------------------------------------------

    def _build_user_chip(self) -> QWidget:
        chip = QWidget()
        chip.setObjectName("userChip")
        chip.setStyleSheet(
            "#userChip { background: transparent; border: 1px solid #45475a; "
            "            border-radius: 14px; padding: 2px 10px 2px 4px; }"
        )
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(2, 0, 4, 0)
        layout.setSpacing(6)

        self._user_avatar_label = QLabel()
        self._user_avatar_label.setFixedSize(22, 22)
        self._user_avatar_label.setPixmap(QPixmap())
        layout.addWidget(self._user_avatar_label)

        self._user_name_label = QLabel("")
        self._user_name_label.setFont(ui_font(10, bold=True))
        self._user_name_label.setStyleSheet(f"color: {FG_SECONDARY.name()};")
        layout.addWidget(self._user_name_label)

        chip.setVisible(False)
        return chip

    def _refresh_user_chip(self) -> None:
        service = self._services.commit
        if service is None or self._repository_path is None:
            self._user_chip.setVisible(False)
            return
        path = self._repository_path

        def _run():
            try:
                return service.get_author(path)
            except Exception:
                return None

        self._submit_read(
            key=f"user_chip::{path}",
            run=_run,
            on_result=self._apply_user_chip,
            error_label="User chip refresh",
        )

    def _apply_user_chip(self, author) -> None:
        if author is None or not (author.name or author.email):
            self._user_chip.setVisible(False)
            return
        pixmap = render_avatar(author.name, author.email, 22)
        self._user_avatar_label.setPixmap(pixmap)
        display_name = author.name or author.email
        self._user_name_label.setText(display_name)
        tooltip = author.name
        if author.email:
            tooltip = f"{author.name} <{author.email}>" if author.name else author.email
        self._user_chip.setToolTip(tooltip)
        self._user_chip.setVisible(True)

    def _open_settings_dialog(self) -> None:
        if self._jobs.busy:
            return
        dialog = SettingsDialog(
            self._services.settings_store,
            self._services.settings,
            self,
        )
        dialog.settings_saved.connect(self._apply_settings)
        dialog.exec()

    def _apply_settings(self, settings: AppSettings) -> None:
        old_git = self._services.settings.git_executable
        self._services.settings = settings
        self._services.crash_reporter.set_enabled(settings.crash_reports_enabled)
        if old_git != settings.git_executable:
            QMessageBox.information(
                self,
                "Restart Required",
                "Git executable validated and saved. Restart GitQt to apply the new Git path; this"
                " session continues using its current executable.",
            )
        self._sidebar.set_split_branch_paths(settings.split_branch_paths)
        if hasattr(self._history_view, "set_row_alpha"):
            self._history_view.set_row_alpha(settings.history_row_alpha)
        if self._working_tree_vm is not None:
            self._working_tree_vm._external_diff_tool_path = (
                settings.external_diff_tool
            )  # noqa: SLF001
            self._working_tree_vm._external_merge_tool_path = (
                settings.external_merge_tool
            )  # noqa: SLF001
        self._apply_ui_font_family(settings.ui_font_family)
        self._log_info("Settings updated.")

    def _apply_ui_font_family(self, family: str) -> None:
        """Push a freshly chosen UI font family to the running app.

        ``QApplication.setFont`` updates widgets that have not explicitly
        overridden their font. Widgets constructed with an explicit
        ``ui_font(...)`` call will pick the new family up on next launch.
        """

        from PySide6.QtWidgets import QApplication

        set_ui_font_family(family)
        app = QApplication.instance()
        if app is None:
            return
        app.setFont(ui_font())

    # -----------------------------------------------------------------------
    # External tool actions
    # -----------------------------------------------------------------------

    def _launch_external_diff(self) -> None:
        if self._working_tree_vm is None:
            return
        vm = self._working_tree_vm.fork()
        self._run_command("External Tool", vm.launch_external_diff_for_selected_file)

    def _launch_external_merge(self) -> None:
        if self._working_tree_vm is None:
            return
        vm = self._working_tree_vm.fork()
        self._run_command("External Tool", vm.launch_external_merge_for_selected_file)

    def _sync_op_log(self) -> None:
        if self._remote_ops_vm:
            self._op_log.set_entries(self._remote_ops_vm.state.operation_log)

    def _log_info(self, message: str) -> None:
        self._op_log.append_message("info", message)

    def _log_error(self, message: str) -> None:
        self._op_log.append_message("error", message)
        self._op_log.expand()
