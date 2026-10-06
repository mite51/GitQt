"""Commit-level operations: cherry-pick, revert, merge, rebase, reset.

Wraps Git CLI invocations behind typed success/failure results so the UI can
surface plain-language error messages without parsing stderr inline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gitqt.services.conflict import ConflictService
from gitqt.services.git import (
    GitCommandFailure,
    GitCommandLaunchError,
    GitCommandTimeoutError,
    GitRunner,
)


class ResetMode(str, Enum):
    """Reset modes supported by :meth:`CommitOperationsService.reset_to`."""

    SOFT = "soft"
    MIXED = "mixed"
    HARD = "hard"


@dataclass(slots=True)
class CommitOperationResult:
    """Outcome of a commit-level operation."""

    success: bool
    message: str


class CommitOperationsService:
    """Run cherry-pick, revert, merge, rebase, and reset operations."""

    def __init__(self, runner: GitRunner) -> None:
        self._runner = runner

    # -----------------------------------------------------------------------
    # Cherry-pick
    # -----------------------------------------------------------------------

    def cherry_pick(
        self,
        repository_path: str | Path,
        commit_hash: str,
        *,
        no_commit: bool = False,
    ) -> CommitOperationResult:
        args = ["cherry-pick"]
        if no_commit:
            args.append("--no-commit")
        args.append(commit_hash)
        return self._run(args, repository_path, "Cherry-pick")

    # -----------------------------------------------------------------------
    # Revert
    # -----------------------------------------------------------------------

    def revert(
        self,
        repository_path: str | Path,
        commit_hash: str,
        *,
        no_commit: bool = False,
    ) -> CommitOperationResult:
        args = ["revert", "--no-edit"]
        if no_commit:
            args.append("--no-commit")
        args.append(commit_hash)
        return self._run(args, repository_path, "Revert")

    # -----------------------------------------------------------------------
    # Merge
    # -----------------------------------------------------------------------

    def merge_commit(
        self,
        repository_path: str | Path,
        commit_hash: str,
        *,
        no_ff: bool = True,
        message: str | None = None,
    ) -> CommitOperationResult:
        args = ["merge"]
        if no_ff:
            args.append("--no-ff")
        if message:
            args.extend(["-m", message])
        args.append(commit_hash)
        return self._run(args, repository_path, "Merge")

    # -----------------------------------------------------------------------
    # Rebase
    # -----------------------------------------------------------------------

    def rebase_onto(
        self,
        repository_path: str | Path,
        commit_hash: str,
    ) -> CommitOperationResult:
        return self._run(["rebase", commit_hash], repository_path, "Rebase")

    # -----------------------------------------------------------------------
    # Reset
    # -----------------------------------------------------------------------

    def reset_to(
        self,
        repository_path: str | Path,
        commit_hash: str,
        mode: ResetMode,
    ) -> CommitOperationResult:
        flag = f"--{mode.value}"
        return self._run(["reset", flag, commit_hash], repository_path, f"Reset ({mode.value})")

    def resolve_operation(
        self, repository_path: str | Path, operation: str, action: str
    ) -> CommitOperationResult:
        """Revalidate the operation before acting on a potentially stale UI banner."""
        if operation not in {"merge", "rebase", "cherry-pick", "revert"}:
            return CommitOperationResult(False, "Unsupported repository operation.")
        if action not in {"continue", "skip", "abort"} or (
            operation == "merge" and action == "skip"
        ):
            return CommitOperationResult(False, "This action is not available for the operation.")
        try:
            snapshot = ConflictService(self._runner).detect_conflicts(repository_path)
            if snapshot.operation != operation:
                return CommitOperationResult(
                    False, "Repository operation changed. Refresh and retry."
                )
            if action == "continue" and snapshot.has_conflicts:
                return CommitOperationResult(
                    False, "Resolve and stage every conflicted file first."
                )
            self._runner.run(
                [operation, f"--{action}"],
                cwd=repository_path,
                env_overrides={"GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true"},
                timeout_seconds=120,
            )
        except GitCommandFailure as exc:
            return CommitOperationResult(False, exc.result.stderr.strip() or str(exc))
        except (GitCommandLaunchError, GitCommandTimeoutError) as exc:
            return CommitOperationResult(False, str(exc))
        return CommitOperationResult(True, f"{operation.capitalize()} {action} completed.")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        repository_path: str | Path,
        op_label: str,
    ) -> CommitOperationResult:
        try:
            self._runner.run(args, cwd=repository_path)
        except GitCommandFailure as exc:
            stderr = exc.result.stderr.strip()
            return CommitOperationResult(
                success=False,
                message=stderr or f"{op_label} failed.",
            )
        return CommitOperationResult(
            success=True,
            message=f"{op_label} succeeded.",
        )
