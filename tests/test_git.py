from __future__ import annotations

import sys
from subprocess import CompletedProcess

import pytest

from gitqt.services.git import (
    GitCommandFailure,
    GitExecutable,
    GitLocator,
    GitNotFoundError,
    GitRunner,
)


def test_git_locator_discovers_git_from_path() -> None:
    locator = GitLocator(which_func=lambda _: sys.executable)

    executable = locator.discover()

    assert executable.path == sys.executable


def test_git_locator_raises_when_git_is_missing() -> None:
    locator = GitLocator(which_func=lambda _: None)

    with pytest.raises(GitNotFoundError):
        locator.discover()


def test_git_runner_returns_command_result() -> None:
    runner = GitRunner(
        executable=GitExecutable(path="git"),
        run_func=lambda *_args, **_kwargs: CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="ok",
            stderr="",
        ),
        monotonic_func=iter([1.0, 1.01]).__next__,
    )

    result = runner.run(["status"], cwd=".")

    assert result.stdout == "ok"
    assert result.exit_code == 0
    assert result.elapsed_ms == 10


def test_git_runner_raises_on_non_zero_exit() -> None:
    runner = GitRunner(
        executable=GitExecutable(path="git"),
        run_func=lambda *_args, **_kwargs: CompletedProcess(
            args=["git", "status"],
            returncode=1,
            stdout="",
            stderr="fatal",
        ),
    )

    with pytest.raises(GitCommandFailure):
        runner.run(["status"], cwd=".")
