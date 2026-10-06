from __future__ import annotations

from pathlib import Path

from gitqt.services.external_tools import ExternalToolService
from gitqt.services.git import GitCommandFailure, GitCommandResult


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail_for: set[str] = set()

    def run(self, args: list[str], **kwargs) -> GitCommandResult:  # noqa: ARG002
        self.calls.append(list(args))
        if any(token in args for token in self.fail_for):
            raise GitCommandFailure(
                GitCommandResult(
                    args=("git", *args),
                    stdout="",
                    stderr="tool launch failed",
                    exit_code=1,
                    elapsed_ms=30,
                )
            )
        return GitCommandResult(
            args=("git", *args),
            stdout="",
            stderr="",
            exit_code=0,
            elapsed_ms=20,
        )


def _make_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_external_tool_service_validates_tool_configuration_paths(tmp_path: Path) -> None:
    runner = _RecordingRunner()
    service = ExternalToolService(runner)  # type: ignore[arg-type]

    missing_tool = tmp_path / "missing-tool"
    merge_directory = tmp_path / "merge-dir"
    merge_directory.mkdir()
    validation = service.validate_configuration(
        diff_tool_path=str(missing_tool),
        merge_tool_path=str(merge_directory),
    )

    assert not validation.is_valid
    assert len(validation.errors) == 2
    assert "does not exist" in validation.errors[0]
    assert "points to a directory" in validation.errors[1]


def test_external_tool_service_launches_git_difftool_with_configured_command(
    tmp_path: Path,
) -> None:
    runner = _RecordingRunner()
    service = ExternalToolService(runner)  # type: ignore[arg-type]
    diff_tool = tmp_path / "diff-tool.sh"
    _make_executable(diff_tool)

    result = service.launch_diff_tool(
        "/tmp/repo",
        tool_path=str(diff_tool),
        file_path="tracked.txt",
        staged=True,
    )

    assert result.success
    assert runner.calls == [
        [
            "-c",
            "diff.tool=gitqt-external",
            "-c",
            f'difftool.gitqt-external.cmd={diff_tool} "$LOCAL" "$REMOTE"',
            "difftool",
            "--no-prompt",
            "--staged",
            "--",
            "tracked.txt",
        ]
    ]


def test_external_tool_service_reports_merge_launch_failures(tmp_path: Path) -> None:
    runner = _RecordingRunner()
    runner.fail_for = {"mergetool"}
    service = ExternalToolService(runner)  # type: ignore[arg-type]
    merge_tool = tmp_path / "merge-tool.sh"
    _make_executable(merge_tool)

    result = service.launch_merge_tool(
        "/tmp/repo",
        tool_path=str(merge_tool),
        file_path="tracked.txt",
    )

    assert not result.success
    assert "failed to launch" in result.message.lower()
    assert "tool launch failed" in result.stderr
