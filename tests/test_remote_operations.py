from __future__ import annotations

from gitqt.app.remote_operations import OPERATION_LOG_LIMIT, RemoteOperationsViewModel
from gitqt.services.remote import (
    RemoteCommandResult,
    RemoteInfo,
    RemoteProgressEvent,
    RemoteSnapshot,
)


class _FakeRemoteService:
    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, str, bool]] = []
        self.pull_calls: list[tuple[str, str, str | None, bool]] = []
        self.push_calls: list[tuple[str, str, str | None, bool, bool]] = []

    def list_remotes(self, repository_path: str) -> RemoteSnapshot:  # noqa: ARG002
        return RemoteSnapshot(
            remotes=[
                RemoteInfo(
                    name="origin",
                    fetch_url="git@github.com:org/repo.git",
                    push_url="git@github.com:org/repo.git",
                )
            ]
        )

    def fetch(self, repository_path: str, *, remote: str, prune: bool) -> RemoteCommandResult:
        self.fetch_calls.append((repository_path, remote, prune))
        return RemoteCommandResult(
            success=True,
            operation="fetch",
            remote=remote,
            message="Fetch completed successfully.",
            elapsed_ms=1000,
            stderr="receiving objects: 100%",
            progress=[
                RemoteProgressEvent(level="info", message="Fetch origin started."),
                RemoteProgressEvent(level="info", message="Fetch origin completed in 1.0s."),
            ],
        )

    def pull(
        self,
        repository_path: str,
        *,
        remote: str,
        branch: str | None,
        rebase: bool,
    ) -> RemoteCommandResult:
        self.pull_calls.append((repository_path, remote, branch, rebase))
        return RemoteCommandResult(
            success=False,
            operation="pull",
            remote=remote,
            message="Pull stopped because Git detected merge conflicts.",
            elapsed_ms=900,
            stderr="CONFLICT (content): Merge conflict in tracked.txt",
            error_category="merge_conflict",
            progress=[
                RemoteProgressEvent(level="info", message="Pull origin started."),
                RemoteProgressEvent(level="error", message="Pull origin failed: merge conflict."),
            ],
        )

    def push(
        self,
        repository_path: str,
        *,
        remote: str,
        branch: str | None,
        set_upstream: bool,
        force_with_lease: bool,
    ) -> RemoteCommandResult:
        self.push_calls.append((repository_path, remote, branch, set_upstream, force_with_lease))
        return RemoteCommandResult(
            success=True,
            operation="push",
            remote=remote,
            message="Push completed successfully.",
            elapsed_ms=1100,
            progress=[
                RemoteProgressEvent(level="info", message="Push origin started."),
                RemoteProgressEvent(level="info", message="Push origin completed in 1.1s."),
            ],
        )


def test_remote_operations_view_model_tracks_progress_and_operation_log() -> None:
    service = _FakeRemoteService()
    view_model = RemoteOperationsViewModel(service)  # type: ignore[arg-type]

    load = view_model.load_repository("/tmp/repo")
    assert load.success
    assert view_model.state.selected_remote == "origin"

    fetch_result = view_model.fetch_selected_remote(prune=True)
    assert fetch_result.success
    assert service.fetch_calls == [("/tmp/repo", "origin", True)]
    assert view_model.state.progress_messages == [
        "Fetch origin started.",
        "Fetch origin completed in 1.0s.",
    ]
    assert view_model.state.operation_log[-1].level == "info"
    assert "receiving objects" in view_model.state.operation_log[-1].detail

    pull_result = view_model.pull_selected_remote(branch="main", rebase=False)
    assert not pull_result.success
    assert service.pull_calls == [("/tmp/repo", "origin", "main", False)]
    assert view_model.state.last_error is not None
    assert "merge conflicts" in view_model.state.last_error.lower()
    assert view_model.state.operation_log[-1].level == "warn"
    assert "conflict" in view_model.state.operation_log[-1].detail.lower()


def test_remote_operations_view_model_requires_selected_remote() -> None:
    service = _FakeRemoteService()
    view_model = RemoteOperationsViewModel(service)  # type: ignore[arg-type]
    view_model.state.repository_path = "/tmp/repo"
    view_model.state.selected_remote = None

    result = view_model.push_selected_remote(branch="main")

    assert not result.success
    assert "no remote is selected" in result.message.lower()


def test_remote_operations_view_model_bounds_operation_log_growth() -> None:
    service = _FakeRemoteService()
    view_model = RemoteOperationsViewModel(service)  # type: ignore[arg-type]
    view_model.load_repository("/tmp/repo")

    for index in range(OPERATION_LOG_LIMIT + 5):
        view_model._append_log("info", f"entry-{index}")  # noqa: SLF001

    assert len(view_model.state.operation_log) == OPERATION_LOG_LIMIT
    assert view_model.state.operation_log[0].summary == "entry-5"
