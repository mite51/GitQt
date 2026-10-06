from __future__ import annotations

from pathlib import Path

from gitqt.services.commit_operations import (
    CommitOperationsService,
    ResetMode,
)
from gitqt.services.git import GitCommandFailure, GitCommandResult


class _StubRunner:
    def __init__(self, *, exit_code: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[str, ...]] = []
        self._exit_code = exit_code
        self._stderr = stderr

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path,  # noqa: ARG002
        input_text: str | None = None,  # noqa: ARG002
    ) -> GitCommandResult:
        key = tuple(args)
        self.calls.append(key)
        result = GitCommandResult(
            args=("git",) + key,
            stdout="",
            stderr=self._stderr,
            exit_code=self._exit_code,
            elapsed_ms=0,
        )
        if self._exit_code != 0:
            raise GitCommandFailure(result)
        return result


def test_cherry_pick_invokes_git_cherry_pick_with_commit_hash() -> None:
    runner = _StubRunner()
    service = CommitOperationsService(runner)  # type: ignore[arg-type]

    result = service.cherry_pick("/tmp/repo", "abc1234")

    assert result.success is True
    assert runner.calls == [("cherry-pick", "abc1234")]


def test_revert_passes_no_edit_and_target_hash() -> None:
    runner = _StubRunner()
    service = CommitOperationsService(runner)  # type: ignore[arg-type]

    service.revert("/tmp/repo", "feed000")

    assert runner.calls == [("revert", "--no-edit", "feed000")]


def test_merge_commit_defaults_to_no_ff() -> None:
    runner = _StubRunner()
    service = CommitOperationsService(runner)  # type: ignore[arg-type]

    service.merge_commit("/tmp/repo", "abc1234")

    assert runner.calls == [("merge", "--no-ff", "abc1234")]


def test_rebase_onto_invokes_git_rebase_with_target() -> None:
    runner = _StubRunner()
    service = CommitOperationsService(runner)  # type: ignore[arg-type]

    service.rebase_onto("/tmp/repo", "abc1234")

    assert runner.calls == [("rebase", "abc1234")]


def test_reset_to_emits_mode_flag_for_each_reset_mode() -> None:
    for mode, flag in (
        (ResetMode.SOFT, "--soft"),
        (ResetMode.MIXED, "--mixed"),
        (ResetMode.HARD, "--hard"),
    ):
        runner = _StubRunner()
        service = CommitOperationsService(runner)  # type: ignore[arg-type]

        service.reset_to("/tmp/repo", "abc1234", mode)

        assert runner.calls == [("reset", flag, "abc1234")]


def test_failed_git_command_produces_unsuccessful_result_with_stderr() -> None:
    runner = _StubRunner(exit_code=1, stderr="could not apply: conflict\n")
    service = CommitOperationsService(runner)  # type: ignore[arg-type]

    result = service.cherry_pick("/tmp/repo", "abc1234")

    assert result.success is False
    assert "conflict" in result.message
