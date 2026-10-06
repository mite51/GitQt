from __future__ import annotations

from gitqt.app.working_tree import WorkingTreeViewModel
from gitqt.parsers.diff import DiffHunk
from gitqt.parsers.status import WorkingTreeEntry, WorkingTreeState
from gitqt.services.commit import CommitAuthor, CommitExecutionResult, CommitValidationResult
from gitqt.services.conflict import ConflictSnapshot
from gitqt.services.external_tools import ExternalToolLaunchResult
from gitqt.services.status import DiffPreview, WorkingTreeSnapshot


class FakeStatusService:
    def __init__(self) -> None:
        self._snapshots = [
            WorkingTreeSnapshot(
                entries=[
                    WorkingTreeEntry(
                        "src/a.py",
                        WorkingTreeState.MODIFIED,
                        staged=False,
                        unstaged=True,
                    ),
                    WorkingTreeEntry(
                        "src/b.py",
                        WorkingTreeState.ADDED,
                        staged=True,
                        unstaged=False,
                    ),
                    WorkingTreeEntry(
                        "src/c.py",
                        WorkingTreeState.UNTRACKED,
                        staged=False,
                        unstaged=True,
                    ),
                ]
            ),
            WorkingTreeSnapshot(
                entries=[
                    WorkingTreeEntry(
                        "src/a.py",
                        WorkingTreeState.MODIFIED,
                        staged=True,
                        unstaged=False,
                    ),
                    WorkingTreeEntry(
                        "src/b.py",
                        WorkingTreeState.ADDED,
                        staged=True,
                        unstaged=False,
                    ),
                    WorkingTreeEntry(
                        "src/c.py",
                        WorkingTreeState.UNTRACKED,
                        staged=False,
                        unstaged=True,
                    ),
                ]
            ),
            WorkingTreeSnapshot(
                entries=[
                    WorkingTreeEntry(
                        "src/a.py",
                        WorkingTreeState.MODIFIED,
                        staged=False,
                        unstaged=True,
                    ),
                    WorkingTreeEntry(
                        "src/b.py",
                        WorkingTreeState.ADDED,
                        staged=True,
                        unstaged=False,
                    ),
                    WorkingTreeEntry(
                        "src/c.py",
                        WorkingTreeState.UNTRACKED,
                        staged=False,
                        unstaged=True,
                    ),
                ]
            ),
            WorkingTreeSnapshot(entries=[]),
        ]
        self._snapshot_index = 0
        self.diff_requests: list[tuple[str, str, bool]] = []
        self.stage_requests: list[tuple[str, str]] = []
        self.unstage_requests: list[tuple[str, str]] = []

    def get_working_tree(
        self,
        repository_path: str,  # noqa: ARG002
        *,
        force_refresh: bool = False,  # noqa: ARG002
    ) -> WorkingTreeSnapshot:
        snapshot = self._snapshots[min(self._snapshot_index, len(self._snapshots) - 1)]
        self._snapshot_index += 1
        return snapshot

    def get_preview(self, repository_path: str, file_path: str, *, staged: bool = False):
        return DiffPreview(self.get_diff(repository_path, file_path, staged=staged))

    def get_diff(
        self,
        repository_path: str,
        file_path: str,
        staged: bool = False,
    ) -> list[DiffHunk]:
        self.diff_requests.append((repository_path, file_path, staged))
        return []

    def stage_file(self, repository_path: str, file_path: str) -> None:
        self.stage_requests.append((repository_path, file_path))

    def unstage_file(self, repository_path: str, file_path: str) -> None:
        self.unstage_requests.append((repository_path, file_path))


class FakeCommitService:
    def __init__(self) -> None:
        self.author_requests: list[str] = []
        self.commit_requests: list[tuple[str, str, str, bool, bool, bool]] = []

    def get_author(self, repository_path: str) -> CommitAuthor:
        self.author_requests.append(repository_path)
        return CommitAuthor(name="Test User", email="test@example.com")

    def validate_message(self, summary: str, body: str = "") -> CommitValidationResult:
        normalized_summary = summary.strip()
        normalized_body = body.strip()
        errors = [] if normalized_summary else ["Commit summary is required."]
        return CommitValidationResult(
            is_valid=not errors,
            errors=errors,
            summary=normalized_summary,
            body=normalized_body,
        )

    def commit(
        self,
        repository_path: str,
        *,
        summary: str,
        body: str,
        allow_empty: bool,
        amend: bool,
        signoff: bool,
    ) -> CommitExecutionResult:
        self.commit_requests.append((repository_path, summary, body, allow_empty, amend, signoff))
        if summary == "reject":
            return CommitExecutionResult(success=False, message="Commit rejected by policy.")
        return CommitExecutionResult(success=True, message="Commit created.", commit_hash="abc123")


class FakeConflictService:
    def __init__(self, *, has_conflicts: bool = False, operation: str = "merge") -> None:
        self._has_conflicts = has_conflicts
        self._operation = operation
        self.requests: list[str] = []

    def detect_conflicts(self, repository_path: str) -> ConflictSnapshot:
        self.requests.append(repository_path)
        if not self._has_conflicts:
            return ConflictSnapshot(
                has_conflicts=False,
                operation=None,
                conflicted_files=[],
                banner_message=None,
                recovery_message=None,
            )
        return ConflictSnapshot(
            has_conflicts=True,
            operation=self._operation,
            conflicted_files=["src/a.py"],
            banner_message=f"{self._operation.title()} conflicts detected in 1 file(s).",
            recovery_message="Resolve and stage conflicts before proceeding.",
        )


class FakeExternalToolService:
    def __init__(self) -> None:
        self.diff_calls: list[tuple[str, str | None, str, bool]] = []
        self.merge_calls: list[tuple[str, str | None, str]] = []

    def launch_diff_tool(
        self,
        repository_path: str,
        *,
        tool_path: str | None,
        file_path: str,
        staged: bool,
    ) -> ExternalToolLaunchResult:
        self.diff_calls.append((repository_path, tool_path, file_path, staged))
        return ExternalToolLaunchResult(success=True, message=f"Diff launched for {file_path}.")

    def launch_merge_tool(
        self,
        repository_path: str,
        *,
        tool_path: str | None,
        file_path: str,
    ) -> ExternalToolLaunchResult:
        self.merge_calls.append((repository_path, tool_path, file_path))
        return ExternalToolLaunchResult(success=True, message=f"Merge launched for {file_path}.")


def test_working_tree_view_model_filters_and_selection() -> None:
    service = FakeStatusService()
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]

    load_result = view_model.load_repository("/tmp/repo")

    assert load_result.success
    assert len(view_model.state.visible_entries) == 3

    filter_result = view_model.set_filters({WorkingTreeState.MODIFIED})
    assert filter_result.success
    assert [entry.path for entry in view_model.state.visible_entries] == ["src/a.py"]

    select_result = view_model.select_file("src/a.py")
    assert select_result.success
    assert view_model.state.selected_file == "src/a.py"
    assert service.diff_requests == [("/tmp/repo", "src/a.py", False)]
    assert view_model.state.load_state == "ready"

    hidden_select_result = view_model.select_file("src/b.py")
    assert not hidden_select_result.success


def test_working_tree_view_model_rejects_empty_filter_set() -> None:
    service = FakeStatusService()
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]
    view_model.load_repository("/tmp/repo")

    result = view_model.set_filters(set())

    assert not result.success
    assert "At least one file state filter" in result.message


def test_working_tree_view_model_tracks_loading_state_during_repository_load() -> None:
    service = FakeStatusService()
    loading_state_seen: list[str] = []

    original_get_working_tree = service.get_working_tree

    def _record_loading_state(
        repository_path: str,  # noqa: ARG001
        *,
        force_refresh: bool = False,  # noqa: ARG001
    ) -> WorkingTreeSnapshot:
        loading_state_seen.append(view_model.state.load_state)
        return original_get_working_tree(repository_path, force_refresh=force_refresh)

    service.get_working_tree = _record_loading_state  # type: ignore[method-assign]
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]

    result = view_model.load_repository("/tmp/repo")

    assert result.success
    assert loading_state_seen == ["loading"]
    assert view_model.state.load_state == "ready"


def test_working_tree_view_model_sets_empty_state_for_clean_working_tree() -> None:
    service = FakeStatusService()
    service._snapshots = [WorkingTreeSnapshot(entries=[])]  # noqa: SLF001
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]

    result = view_model.load_repository("/tmp/repo")

    assert result.success
    assert view_model.state.load_state == "empty"
    assert view_model.state.message == "Working tree is clean."


def test_working_tree_view_model_sets_error_state_when_load_fails() -> None:
    class _FailingStatusService(FakeStatusService):
        def get_working_tree(
            self,
            repository_path: str,  # noqa: ARG002
            *,
            force_refresh: bool = False,  # noqa: ARG002
        ) -> WorkingTreeSnapshot:
            raise RuntimeError("status refresh failed")

    service = _FailingStatusService()
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]

    result = view_model.load_repository("/tmp/repo")

    assert not result.success
    assert view_model.state.load_state == "error"
    assert "status refresh failed" in (view_model.state.last_error or "")
    assert "unable to load working tree" in view_model.state.message.lower()


def test_stage_selected_file_refreshes_snapshot_and_selection() -> None:
    service = FakeStatusService()
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]
    view_model.load_repository("/tmp/repo")
    view_model.select_file("src/a.py")

    result = view_model.stage_selected_file()

    assert result.success
    assert service.stage_requests == [("/tmp/repo", "src/a.py")]
    assert view_model.state.selected_file == "src/a.py"
    assert service.diff_requests[-1] == ("/tmp/repo", "src/a.py", True)
    selected = next(entry for entry in view_model.state.entries if entry.path == "src/a.py")
    assert selected.staged
    assert not selected.unstaged


def test_unstage_selected_file_refreshes_snapshot_and_selection() -> None:
    service = FakeStatusService()
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]
    view_model.load_repository("/tmp/repo")
    view_model.select_file("src/a.py")
    view_model.stage_selected_file()

    result = view_model.unstage_selected_file()

    assert result.success
    assert service.unstage_requests == [("/tmp/repo", "src/a.py")]
    selected = next(entry for entry in view_model.state.entries if entry.path == "src/a.py")
    assert not selected.staged
    assert selected.unstaged


def test_stage_selected_file_requires_selected_unstaged_changes() -> None:
    service = FakeStatusService()
    view_model = WorkingTreeViewModel(service)  # type: ignore[arg-type]
    view_model.load_repository("/tmp/repo")
    view_model.select_file("src/b.py")

    result = view_model.stage_selected_file()

    assert not result.success
    assert "no unstaged changes" in result.message.lower()


def test_commit_changes_refreshes_working_tree_and_clears_form() -> None:
    status_service = FakeStatusService()
    commit_service = FakeCommitService()
    view_model = WorkingTreeViewModel(
        status_service,  # type: ignore[arg-type]
        commit_service,  # type: ignore[arg-type]
    )
    view_model.load_repository("/tmp/repo")

    view_model.state.commit_summary = "Ship session 4"
    view_model.state.commit_body = "Add commit flow"
    result = view_model.commit_changes(signoff=True)

    assert result.success
    assert commit_service.author_requests == ["/tmp/repo"]
    assert commit_service.commit_requests == [
        ("/tmp/repo", "Ship session 4", "Add commit flow", False, False, True)
    ]
    assert view_model.state.commit_summary == ""
    assert view_model.state.commit_body == ""
    assert len(view_model.state.entries) == 3


def test_commit_changes_rejects_invalid_message() -> None:
    status_service = FakeStatusService()
    commit_service = FakeCommitService()
    view_model = WorkingTreeViewModel(
        status_service,  # type: ignore[arg-type]
        commit_service,  # type: ignore[arg-type]
    )
    view_model.load_repository("/tmp/repo")

    result = view_model.set_commit_message("   ")

    assert not result.success
    assert "summary is required" in result.message.lower()


def test_working_tree_view_model_allows_staging_resolutions_but_blocks_commit() -> None:
    status_service = FakeStatusService()
    commit_service = FakeCommitService()
    conflict_service = FakeConflictService(has_conflicts=True, operation="rebase")
    view_model = WorkingTreeViewModel(
        status_service,  # type: ignore[arg-type]
        commit_service,  # type: ignore[arg-type]
        conflict_service,  # type: ignore[arg-type]
    )

    load_result = view_model.load_repository("/tmp/repo")

    assert load_result.success
    assert view_model.state.has_conflicts
    assert view_model.state.conflict_banner is not None
    assert "rebase conflicts" in view_model.state.conflict_banner.lower()
    assert (
        "resolve and stage conflicts" in (view_model.state.conflict_recovery_message or "").lower()
    )
    assert view_model.state.stage_actions_enabled
    assert not view_model.state.commit_enabled

    view_model.select_file("src/a.py")
    stage_result = view_model.stage_selected_file()
    commit_result = view_model.commit_changes()
    assert stage_result.success
    assert not commit_result.success
    assert "continue" in commit_result.message.lower()


def test_working_tree_view_model_launches_external_diff_and_merge_for_selected_file() -> None:
    status_service = FakeStatusService()
    external_tools = FakeExternalToolService()
    view_model = WorkingTreeViewModel(
        status_service,  # type: ignore[arg-type]
        external_tool_service=external_tools,  # type: ignore[arg-type]
        external_diff_tool_path="/usr/bin/diff-tool",
        external_merge_tool_path="/usr/bin/merge-tool",
    )
    view_model.load_repository("/tmp/repo")
    view_model.select_file("src/a.py")

    diff_result = view_model.launch_external_diff_for_selected_file()
    merge_result = view_model.launch_external_merge_for_selected_file()

    assert diff_result.success
    assert merge_result.success
    assert external_tools.diff_calls == [("/tmp/repo", "/usr/bin/diff-tool", "src/a.py", False)]
    assert external_tools.merge_calls == [("/tmp/repo", "/usr/bin/merge-tool", "src/a.py")]
