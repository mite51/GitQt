from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from gitqt.services.remote import RemoteCommandResult, RemoteInfo, RemoteService

OPERATION_LOG_LIMIT = 250


@dataclass(slots=True)
class OperationLogEntry:
    timestamp: datetime
    level: str
    summary: str
    detail: str = ""


@dataclass(slots=True)
class RemoteOperationsActionResult:
    success: bool
    message: str


@dataclass(slots=True)
class RemoteOperationsState:
    repository_path: str | None = None
    remotes: list[RemoteInfo] = field(default_factory=list)
    selected_remote: str | None = None
    progress_messages: list[str] = field(default_factory=list)
    operation_log: list[OperationLogEntry] = field(default_factory=list)
    last_error: str | None = None
    message: str = "Open a repository to inspect remotes."


class RemoteOperationsViewModel:
    """State and workflows for remote inspection and sync operations."""

    def __init__(self, remote_service: RemoteService) -> None:
        self._remote_service = remote_service
        self.state = RemoteOperationsState()

    def load_repository(self, repository_path: str) -> RemoteOperationsActionResult:
        snapshot = self._remote_service.list_remotes(repository_path)
        selected_remote = snapshot.remotes[0].name if snapshot.remotes else None
        self.state.repository_path = repository_path
        self.state.remotes = list(snapshot.remotes)
        self.state.selected_remote = selected_remote
        self.state.progress_messages = []
        self.state.operation_log = []
        self.state.last_error = None
        count = len(snapshot.remotes)
        if count == 0:
            self.state.message = "No remotes configured for this repository."
        elif count == 1:
            self.state.message = "1 remote configured."
        else:
            self.state.message = f"{count} remotes configured."
        self._append_log("info", f"Loaded {count} remotes.")
        return RemoteOperationsActionResult(success=True, message="Remote information loaded.")

    def select_remote(self, remote_name: str) -> RemoteOperationsActionResult:
        if remote_name not in {remote.name for remote in self.state.remotes}:
            return RemoteOperationsActionResult(
                success=False, message="Selected remote is not available."
            )
        self.state.selected_remote = remote_name
        self.state.message = f"Selected remote: {remote_name}"
        return RemoteOperationsActionResult(success=True, message="Remote selection updated.")

    def fetch_selected_remote(self, *, prune: bool = False) -> RemoteOperationsActionResult:
        return self._run_for_selected_remote("fetch", prune=prune)

    def pull_selected_remote(
        self,
        *,
        branch: str | None = None,
        rebase: bool = False,
    ) -> RemoteOperationsActionResult:
        return self._run_for_selected_remote("pull", branch=branch, rebase=rebase)

    def push_selected_remote(
        self,
        *,
        branch: str | None = None,
        set_upstream: bool = False,
        force_with_lease: bool = False,
    ) -> RemoteOperationsActionResult:
        return self._run_for_selected_remote(
            "push",
            branch=branch,
            set_upstream=set_upstream,
            force_with_lease=force_with_lease,
        )

    def clear_operation_log(self) -> None:
        self.state.operation_log = []
        self.state.message = "Operation log cleared."

    def _run_for_selected_remote(
        self,
        operation: str,
        **options: bool | str | None,
    ) -> RemoteOperationsActionResult:
        if self.state.repository_path is None:
            return RemoteOperationsActionResult(success=False, message="No repository is loaded.")
        if self.state.selected_remote is None:
            return RemoteOperationsActionResult(success=False, message="No remote is selected.")

        remote = self.state.selected_remote
        if operation == "fetch":
            result = self._remote_service.fetch(
                self.state.repository_path,
                remote=remote,
                prune=bool(options.get("prune", False)),
            )
        elif operation == "pull":
            result = self._remote_service.pull(
                self.state.repository_path,
                remote=remote,
                branch=options.get("branch"),  # type: ignore[arg-type]
                rebase=bool(options.get("rebase", False)),
            )
        elif operation == "push":
            result = self._remote_service.push(
                self.state.repository_path,
                remote=remote,
                branch=options.get("branch"),  # type: ignore[arg-type]
                set_upstream=bool(options.get("set_upstream", False)),
                force_with_lease=bool(options.get("force_with_lease", False)),
            )
        else:
            raise ValueError(f"Unsupported operation: {operation}")

        return self._consume_remote_result(result)

    def _consume_remote_result(self, result: RemoteCommandResult) -> RemoteOperationsActionResult:
        self.state.progress_messages = [event.message for event in result.progress or []]
        if result.success:
            self.state.last_error = None
            self.state.message = result.message
            self._append_log("info", result.message, detail=result.stderr)
            return RemoteOperationsActionResult(success=True, message=result.message)

        self.state.last_error = result.message
        self.state.message = result.message
        self._append_log("warn", result.message, detail=result.stderr)
        return RemoteOperationsActionResult(success=False, message=result.message)

    def _append_log(self, level: str, summary: str, *, detail: str = "") -> None:
        self.state.operation_log.append(
            OperationLogEntry(
                timestamp=datetime.now(tz=UTC),
                level=level,
                summary=summary,
                detail=detail,
            )
        )
        if len(self.state.operation_log) > OPERATION_LOG_LIMIT:
            overflow_count = len(self.state.operation_log) - OPERATION_LOG_LIMIT
            self.state.operation_log = self.state.operation_log[overflow_count:]
