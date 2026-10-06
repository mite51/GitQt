from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, field

from gitqt.parsers.diff import DiffHunk
from gitqt.parsers.status import WorkingTreeEntry, WorkingTreeState
from gitqt.services.commit import CommitAuthor, CommitService
from gitqt.services.conflict import ConflictService, ConflictSnapshot
from gitqt.services.external_tools import ExternalToolService
from gitqt.services.git import GitCommandFailure
from gitqt.services.status import DiffPreview, StatusService, WorkingTreeSnapshot


@dataclass(slots=True)
class WorkingTreeActionResult:
    success: bool
    message: str


@dataclass(slots=True)
class WorkingTreeStateModel:
    repository_path: str | None = None
    entries: list[WorkingTreeEntry] = field(default_factory=list)
    visible_entries: list[WorkingTreeEntry] = field(default_factory=list)
    selected_file: str | None = None
    selected_staged: bool = False
    selected_preview: DiffPreview = field(default_factory=lambda: DiffPreview([]))
    selected_diff: list[DiffHunk] = field(default_factory=list)
    active_filters: set[WorkingTreeState] = field(
        default_factory=lambda: {
            WorkingTreeState.MODIFIED,
            WorkingTreeState.ADDED,
            WorkingTreeState.DELETED,
            WorkingTreeState.UNTRACKED,
        }
    )
    commit_summary: str = ""
    commit_body: str = ""
    commit_validation_errors: list[str] = field(default_factory=list)
    commit_author: CommitAuthor = field(default_factory=lambda: CommitAuthor(name="", email=""))
    has_conflicts: bool = False
    conflicted_files: list[str] = field(default_factory=list)
    conflict_banner: str | None = None
    conflict_recovery_message: str | None = None
    conflict_operation: str | None = None
    stage_actions_enabled: bool = True
    commit_enabled: bool = True
    load_state: str = "idle"
    last_error: str | None = None
    message: str = "Open a repository to view file changes."


class WorkingTreeViewModel:
    """State and actions for working tree status and inline diff browsing."""

    def __init__(
        self,
        status_service: StatusService,
        commit_service: CommitService | None = None,
        conflict_service: ConflictService | None = None,
        external_tool_service: ExternalToolService | None = None,
        external_diff_tool_path: str | None = None,
        external_merge_tool_path: str | None = None,
    ) -> None:
        self._status_service = status_service
        self._commit_service = commit_service
        self._conflict_service = conflict_service
        self._external_tool_service = external_tool_service
        self._external_diff_tool_path = external_diff_tool_path
        self._external_merge_tool_path = external_merge_tool_path
        self.state = WorkingTreeStateModel()

    def fork(self) -> WorkingTreeViewModel:
        """A job owns its model state; services are shared, widgets never are."""
        model = copy(self)
        model.state = deepcopy(self.state)
        return model

    def adopt(self, model: WorkingTreeViewModel, *, keep_draft: bool = True) -> None:
        summary, body = self.state.commit_summary, self.state.commit_body
        filters = self.state.active_filters
        self.state = model.state
        self.state.active_filters = filters
        if keep_draft:
            self.state.commit_summary, self.state.commit_body = summary, body
        message = self.state.message
        self._apply_visibility()
        self.state.message = message

    def load_repository(self, repository_path: str) -> WorkingTreeActionResult:
        if repository_path != self.state.repository_path:
            self.state.selected_file = None
            self.state.selected_diff = []
            self.state.selected_preview = DiffPreview([])
        self.state.load_state = "loading"
        self.state.last_error = None
        self.state.message = "Loading repository changes..."
        try:
            snapshot = self._status_service.get_working_tree(repository_path)
        except Exception as exc:  # pragma: no cover - exercised with fakes in unit tests
            self.state.repository_path = repository_path
            self.state.entries = []
            self.state.visible_entries = []
            self.state.selected_file = None
            self.state.selected_diff = []
            self.state.load_state = "error"
            self.state.last_error = str(exc)
            self.state.message = f"Unable to load working tree: {exc}"
            self._apply_conflict_snapshot(None)
            return WorkingTreeActionResult(success=False, message=self.state.message)
        self._apply_snapshot(repository_path=repository_path, snapshot=snapshot)
        self._refresh_conflict_state()
        if self.state.selected_file is not None:
            self.select_file(self.state.selected_file, staged=self.state.selected_staged)
        if self._commit_service is not None:
            self.state.commit_author = self._commit_service.get_author(repository_path)
        return WorkingTreeActionResult(success=True, message="Working tree loaded.")

    def set_filters(self, filters: set[WorkingTreeState]) -> WorkingTreeActionResult:
        if not filters:
            return WorkingTreeActionResult(
                success=False,
                message="At least one file state filter must remain selected.",
            )
        self.state.active_filters = set(filters)
        self._apply_visibility()
        return WorkingTreeActionResult(success=True, message="File state filters updated.")

    def select_file(self, file_path: str, *, staged: bool | None = None) -> WorkingTreeActionResult:
        if self.state.repository_path is None:
            return WorkingTreeActionResult(success=False, message="No repository is loaded.")

        if file_path not in {entry.path for entry in self.state.visible_entries}:
            return WorkingTreeActionResult(
                success=False,
                message="Selected file is not currently visible.",
            )

        self.state.selected_file = file_path
        selected_entry = self._find_entry(file_path)
        if selected_entry is None:
            return WorkingTreeActionResult(success=False, message="Selected file is not available.")
        self.state.selected_staged = (
            selected_entry.staged and not selected_entry.unstaged if staged is None else staged
        )
        self.state.selected_preview = self._status_service.get_preview(
            self.state.repository_path,
            file_path,
            staged=self.state.selected_staged,
        )
        self.state.selected_diff = self.state.selected_preview.hunks
        self.state.message = f"Viewing diff for {file_path}"
        return WorkingTreeActionResult(success=True, message="Diff loaded for selected file.")

    def stage_selected_file(self) -> WorkingTreeActionResult:
        if not self.state.stage_actions_enabled:
            return WorkingTreeActionResult(
                success=False,
                message="Stage actions are disabled while conflicts are unresolved.",
            )
        if self.state.repository_path is None:
            return WorkingTreeActionResult(success=False, message="No repository is loaded.")
        entry = self._selected_entry()
        if entry is None:
            return WorkingTreeActionResult(success=False, message="No file is selected.")
        if not entry.unstaged:
            return WorkingTreeActionResult(
                success=False,
                message="Selected file has no unstaged changes.",
            )

        self._status_service.stage_file(self.state.repository_path, entry.path)
        self._refresh_after_change(selected_path=entry.path)
        self.state.message = f"Staged changes for {entry.path}"
        return WorkingTreeActionResult(success=True, message="Selected file changes staged.")

    def unstage_selected_file(self) -> WorkingTreeActionResult:
        if not self.state.stage_actions_enabled:
            return WorkingTreeActionResult(
                success=False,
                message="Unstage actions are disabled while conflicts are unresolved.",
            )
        if self.state.repository_path is None:
            return WorkingTreeActionResult(success=False, message="No repository is loaded.")
        entry = self._selected_entry()
        if entry is None:
            return WorkingTreeActionResult(success=False, message="No file is selected.")
        if not entry.staged:
            return WorkingTreeActionResult(
                success=False,
                message="Selected file has no staged changes.",
            )

        self._status_service.unstage_file(self.state.repository_path, entry.path)
        self._refresh_after_change(selected_path=entry.path)
        self.state.message = f"Unstaged changes for {entry.path}"
        return WorkingTreeActionResult(success=True, message="Selected file changes unstaged.")

    def change_files(self, paths: list[str], *, stage: bool) -> WorkingTreeActionResult:
        if self.state.repository_path is None:
            return WorkingTreeActionResult(False, "No repository is loaded.")
        eligible = {
            entry.path
            for entry in self.state.entries
            if (entry.unstaged if stage else entry.staged)
        }
        selected = list(dict.fromkeys(path for path in paths if path in eligible))
        if not selected:
            return WorkingTreeActionResult(False, "Select files with changes on this side.")
        completed = 0
        try:
            for path in selected:
                method = (
                    self._status_service.stage_file if stage else self._status_service.unstage_file
                )
                method(self.state.repository_path, path)
                completed += 1
        except Exception as exc:
            message = exc.result.stderr.strip() if isinstance(exc, GitCommandFailure) else str(exc)
            return WorkingTreeActionResult(False, f"Updated {completed} file(s). {message}")
        finally:
            self._refresh_after_change(selected_path=self.state.selected_file)
        verb = "Staged" if stage else "Unstaged"
        return WorkingTreeActionResult(True, f"{verb} {completed} file(s).")

    def change_hunk(
        self,
        hunk_index: int,
        *,
        action: str,
        selected_lines: set[int] | None = None,
    ) -> WorkingTreeActionResult:
        if self.state.repository_path is None or self.state.selected_file is None:
            return WorkingTreeActionResult(False, "No file is selected.")
        if (action == "unstage") != self.state.selected_staged:
            return WorkingTreeActionResult(False, "Select the correct side of the file first.")
        if self.state.has_conflicts:
            return WorkingTreeActionResult(False, "Resolve conflicts and stage whole files first.")
        try:
            backup = self._status_service.apply_hunk(
                self.state.repository_path,
                self.state.selected_file,
                self.state.selected_preview,
                hunk_index,
                action=action,
                selected_lines=selected_lines,
            )
        except Exception as exc:
            message = exc.result.stderr.strip() if isinstance(exc, GitCommandFailure) else str(exc)
            return WorkingTreeActionResult(False, message)
        finally:
            self._refresh_after_change(selected_path=self.state.selected_file)
        message = f"{action.capitalize()} completed."
        if backup:
            message += f" Original file backup: {backup}"
        self.state.message = message
        return WorkingTreeActionResult(True, message)

    def set_commit_message(self, summary: str, body: str = "") -> WorkingTreeActionResult:
        if self._commit_service is None:
            return WorkingTreeActionResult(
                success=False,
                message="Commit workflow is unavailable because Git is not configured.",
            )
        validation = self._commit_service.validate_message(summary, body)
        self.state.commit_summary = validation.summary
        self.state.commit_body = validation.body
        self.state.commit_validation_errors = list(validation.errors)
        if validation.is_valid:
            return WorkingTreeActionResult(success=True, message="Commit message is valid.")
        return WorkingTreeActionResult(
            success=False,
            message=" ".join(validation.errors),
        )

    def commit_changes(
        self,
        *,
        allow_empty: bool = False,
        amend: bool = False,
        signoff: bool = False,
        expected_head: str | None = None,
    ) -> WorkingTreeActionResult:
        if not self.state.commit_enabled:
            return WorkingTreeActionResult(
                success=False,
                message="Resolve conflicts and use Continue to finish the active operation.",
            )
        if self.state.repository_path is None:
            return WorkingTreeActionResult(success=False, message="No repository is loaded.")
        if self._commit_service is None:
            return WorkingTreeActionResult(
                success=False,
                message="Commit workflow is unavailable because Git is not configured.",
            )

        validation_result = self.set_commit_message(
            summary=self.state.commit_summary,
            body=self.state.commit_body,
        )
        if not validation_result.success:
            return validation_result

        result = self._commit_service.commit(
            self.state.repository_path,
            summary=self.state.commit_summary,
            body=self.state.commit_body,
            allow_empty=allow_empty,
            amend=amend,
            signoff=signoff,
            **({"expected_head": expected_head} if expected_head is not None else {}),
        )
        if not result.success:
            self.state.message = result.message
            return WorkingTreeActionResult(success=False, message=result.message)

        self.state.commit_summary = ""
        self.state.commit_body = ""
        self.state.commit_validation_errors = []
        self._refresh_after_change(selected_path=None)
        self.state.message = (
            f"Commit {result.commit_hash} created." if result.commit_hash else "Commit created."
        )
        return WorkingTreeActionResult(success=True, message=self.state.message)

    def launch_external_diff_for_selected_file(self) -> WorkingTreeActionResult:
        if self.state.repository_path is None:
            return WorkingTreeActionResult(success=False, message="No repository is loaded.")
        if self._external_tool_service is None:
            return WorkingTreeActionResult(success=False, message="External tools are unavailable.")
        entry = self._selected_entry()
        if entry is None:
            return WorkingTreeActionResult(success=False, message="No file is selected.")
        result = self._external_tool_service.launch_diff_tool(
            self.state.repository_path,
            tool_path=self._external_diff_tool_path,
            file_path=entry.path,
            staged=self.state.selected_staged,
        )
        self.state.message = result.message
        return WorkingTreeActionResult(success=result.success, message=result.message)

    def launch_external_merge_for_selected_file(self) -> WorkingTreeActionResult:
        if self.state.repository_path is None:
            return WorkingTreeActionResult(success=False, message="No repository is loaded.")
        if self._external_tool_service is None:
            return WorkingTreeActionResult(success=False, message="External tools are unavailable.")
        entry = self._selected_entry()
        if entry is None:
            return WorkingTreeActionResult(success=False, message="No file is selected.")
        result = self._external_tool_service.launch_merge_tool(
            self.state.repository_path,
            tool_path=self._external_merge_tool_path,
            file_path=entry.path,
        )
        self.state.message = result.message
        return WorkingTreeActionResult(success=result.success, message=result.message)

    def _apply_snapshot(self, repository_path: str, snapshot: WorkingTreeSnapshot) -> None:
        self.state.repository_path = repository_path
        self.state.entries = list(snapshot.entries)
        self._apply_visibility()

    def _refresh_after_change(self, *, selected_path: str | None) -> None:
        if self.state.repository_path is None:
            return
        self.state.load_state = "loading"
        self.state.last_error = None
        try:
            snapshot = self._status_service.get_working_tree(
                self.state.repository_path,
                force_refresh=True,
            )
        except Exception as exc:  # pragma: no cover - exercised with fakes in unit tests
            self.state.load_state = "error"
            self.state.last_error = str(exc)
            self.state.message = f"Unable to refresh working tree: {exc}"
            return
        self._apply_snapshot(repository_path=self.state.repository_path, snapshot=snapshot)
        self._refresh_conflict_state()
        if selected_path is not None and selected_path in {
            entry.path for entry in self.state.visible_entries
        }:
            entry = self._find_entry(selected_path)
            side = self.state.selected_staged
            if entry is not None and not (entry.staged if side else entry.unstaged):
                side = not side
            self.select_file(selected_path, staged=side)

    def _find_entry(self, file_path: str) -> WorkingTreeEntry | None:
        for entry in self.state.entries:
            if entry.path == file_path:
                return entry
        return None

    def _selected_entry(self) -> WorkingTreeEntry | None:
        if self.state.selected_file is None:
            return None
        return self._find_entry(self.state.selected_file)

    def _apply_visibility(self) -> None:
        self.state.visible_entries = [
            entry for entry in self.state.entries if entry.state in self.state.active_filters
        ]

        if self.state.selected_file not in {entry.path for entry in self.state.visible_entries}:
            self.state.selected_file = None
            self.state.selected_diff = []
            self.state.selected_preview = DiffPreview([])

        visible_count = len(self.state.visible_entries)
        total_count = len(self.state.entries)
        if total_count == 0:
            self.state.load_state = "empty"
            self.state.message = "Working tree is clean."
            return
        self.state.load_state = "ready"
        if visible_count == 0:
            self.state.message = "No files match the active filters."
        elif visible_count == 1:
            self.state.message = "1 file change visible."
        else:
            self.state.message = f"{visible_count} file changes visible."

    def _refresh_conflict_state(self) -> None:
        if self.state.repository_path is None or self._conflict_service is None:
            self._apply_conflict_snapshot(None)
            return
        detect_fast = getattr(self._conflict_service, "detect_from_snapshot", None)
        if callable(detect_fast):
            snapshot = detect_fast(self.state.repository_path, self.state.entries)
        else:
            snapshot = self._conflict_service.detect_conflicts(self.state.repository_path)
        self._apply_conflict_snapshot(snapshot)

    def _apply_conflict_snapshot(self, snapshot: ConflictSnapshot | None) -> None:
        if snapshot is None:
            self.state.has_conflicts = False
            self.state.conflicted_files = []
            self.state.conflict_banner = None
            self.state.conflict_recovery_message = None
            self.state.conflict_operation = None
            self.state.stage_actions_enabled = True
            self.state.commit_enabled = True
            return

        self.state.has_conflicts = snapshot.has_conflicts
        self.state.conflicted_files = list(snapshot.conflicted_files)
        self.state.conflict_banner = snapshot.banner_message
        self.state.conflict_recovery_message = snapshot.recovery_message
        self.state.conflict_operation = snapshot.operation
        # Staging is how users mark conflict resolutions; never disable it here.
        self.state.stage_actions_enabled = True
        self.state.commit_enabled = not snapshot.has_conflicts and snapshot.operation is None
