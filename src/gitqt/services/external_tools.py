from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from gitqt.services.git import GitCommandFailure, GitRunner


@dataclass(slots=True)
class ExternalToolValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExternalToolLaunchResult:
    success: bool
    message: str
    stderr: str = ""


class ExternalToolService:
    """Validate and launch configured external diff and merge tools."""

    def __init__(self, runner: GitRunner) -> None:
        self._runner = runner

    def validate_configuration(
        self,
        *,
        diff_tool_path: str | None,
        merge_tool_path: str | None,
    ) -> ExternalToolValidationResult:
        errors: list[str] = []
        diff_error = self._validate_tool_path(diff_tool_path, label="diff")
        if diff_error:
            errors.append(diff_error)
        merge_error = self._validate_tool_path(merge_tool_path, label="merge")
        if merge_error:
            errors.append(merge_error)
        return ExternalToolValidationResult(is_valid=not errors, errors=errors)

    def launch_diff_tool(
        self,
        repository_path: str | Path,
        *,
        tool_path: str | None,
        file_path: str,
        staged: bool = False,
    ) -> ExternalToolLaunchResult:
        validation_error = self._validate_tool_path(tool_path, label="diff")
        if validation_error:
            return ExternalToolLaunchResult(success=False, message=validation_error)
        assert tool_path is not None

        command = f'{shlex.quote(str(Path(tool_path).expanduser()))} "$LOCAL" "$REMOTE"'
        args = [
            "-c",
            "diff.tool=gitqt-external",
            "-c",
            f"difftool.gitqt-external.cmd={command}",
            "difftool",
            "--no-prompt",
        ]
        if staged:
            args.append("--staged")
        args.extend(["--", file_path])
        try:
            self._runner.run(
                args, cwd=repository_path, env_overrides={"GIT_LITERAL_PATHSPECS": "1"}
            )
        except GitCommandFailure as exc:
            stderr = exc.result.stderr.strip()
            return ExternalToolLaunchResult(
                success=False,
                message="Failed to launch the configured external diff tool.",
                stderr=stderr,
            )
        return ExternalToolLaunchResult(
            success=True,
            message=f"Launched external diff tool for {file_path}.",
        )

    def launch_merge_tool(
        self,
        repository_path: str | Path,
        *,
        tool_path: str | None,
        file_path: str,
    ) -> ExternalToolLaunchResult:
        # Git's mergetool shell script expands its intermediate filename list.
        # Literal pathspecs do not prevent that shell expansion on supported Git
        # versions, so refuse unsafe names before any tool or index mutation.
        if any(char in '*?["\\' or ord(char) < 32 or ord(char) == 127 for char in file_path):
            return ExternalToolLaunchResult(
                success=False,
                message="Git's external merge launcher cannot safely handle this filename. "
                "Resolve it in your editor, then stage the file in GitQt.",
            )
        validation_error = self._validate_tool_path(tool_path, label="merge")
        if validation_error:
            return ExternalToolLaunchResult(success=False, message=validation_error)
        assert tool_path is not None

        command = (
            f"{shlex.quote(str(Path(tool_path).expanduser()))}"
            ' "$BASE" "$LOCAL" "$REMOTE" "$MERGED"'
        )
        args = [
            "-c",
            "merge.tool=gitqt-external",
            "-c",
            "core.quotePath=false",
            "-c",
            f"mergetool.gitqt-external.cmd={command}",
            "mergetool",
            "--no-prompt",
            "--",
            file_path,
        ]
        try:
            self._runner.run(
                args, cwd=repository_path, env_overrides={"GIT_LITERAL_PATHSPECS": "1"}
            )
        except GitCommandFailure as exc:
            stderr = exc.result.stderr.strip()
            return ExternalToolLaunchResult(
                success=False,
                message="Failed to launch the configured external merge tool.",
                stderr=stderr,
            )
        return ExternalToolLaunchResult(
            success=True,
            message=f"Launched external merge tool for {file_path}.",
        )

    def _validate_tool_path(self, tool_path: str | None, *, label: str) -> str | None:
        if not tool_path:
            return f"No external {label} tool is configured."
        expanded = Path(tool_path).expanduser()
        if not expanded.exists():
            return f"Configured external {label} tool does not exist: {expanded}"
        if expanded.is_dir():
            return f"Configured external {label} tool points to a directory: {expanded}"
        if not os.access(expanded, os.X_OK):
            return f"Configured external {label} tool is not executable: {expanded}"
        return None
