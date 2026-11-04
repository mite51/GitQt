from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gitqt.parsers.diff import DiffHunk, DiffLineType, parse_unified_diff
from gitqt.parsers.patch import build_patch_for_hunk
from gitqt.parsers.status import WorkingTreeEntry, parse_porcelain_status
from gitqt.services.git import GitCommandFailure, GitOutputLimitError, GitRunner


@dataclass(slots=True)
class WorkingTreeSnapshot:
    entries: list[WorkingTreeEntry]


@dataclass(slots=True)
class DiffPreview:
    hunks: list[DiffHunk]
    message: str = ""
    partial_supported: bool = False
    raw: str = ""
    new_file: bool = False
    deleted_file: bool = False
    mode: str = "100644"


class StatusService:
    """Load working tree status and apply explicitly selected changes."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        cache_ttl_seconds: float = 0.35,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._cache_ttl_seconds = max(cache_ttl_seconds, 0.0)
        self._monotonic = monotonic_func or time.monotonic
        self._working_tree_cache: dict[str, tuple[float, WorkingTreeSnapshot]] = {}
        self._diff_cache: dict[tuple[str, str, bool], tuple[float, DiffPreview]] = {}

    def get_working_tree(
        self,
        repository_path: str | Path,
        *,
        force_refresh: bool = False,
    ) -> WorkingTreeSnapshot:
        key = str(Path(repository_path))
        cached = self._working_tree_cache.get(key)
        if not force_refresh and cached and cached[0] > self._monotonic():
            return cached[1]
        result = self._runner.run(
            ["status", "--porcelain", "-z", "--untracked-files=all"], cwd=repository_path
        )
        entries = parse_porcelain_status(result.stdout)
        if entries:
            try:
                attributes = self._runner.run(
                    ["check-attr", "-z", "filter", "--", *(entry.path for entry in entries)],
                    cwd=repository_path,
                    env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
                ).stdout.split("\0")
            except Exception:
                attributes = []
            lfs_paths = {
                attributes[index]
                for index in range(0, len(attributes) - 2, 3)
                if attributes[index + 1] == "filter" and attributes[index + 2] == "lfs"
            }
            for entry in entries:
                entry.lfs_tracked = entry.path in lfs_paths
        snapshot = WorkingTreeSnapshot(entries)
        self._working_tree_cache[key] = (self._monotonic() + self._cache_ttl_seconds, snapshot)
        return snapshot

    def stage_file(self, repository_path: str | Path, file_path: str) -> None:
        self._runner.run(
            ["add", "--", file_path],
            cwd=repository_path,
            env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
        )
        self.invalidate_repository(repository_path)

    def unstage_file(self, repository_path: str | Path, file_path: str) -> None:
        paths = self._staged_paths(repository_path, file_path)
        try:
            self._runner.run(
                ["restore", "--staged", "--", *paths],
                cwd=repository_path,
                env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
            )
        except GitCommandFailure as exc:
            if "could not resolve HEAD" not in exc.result.stderr:
                raise
            self._runner.run(
                ["rm", "--cached", "-f", "--", file_path],
                cwd=repository_path,
                env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
            )
        self.invalidate_repository(repository_path)

    def discard_files(self, repository_path: str | Path, file_paths: list[str]) -> list[str]:
        """Discard whole-file worktree changes after retaining local recovery copies."""
        root = Path(repository_path).resolve()
        unique = list(dict.fromkeys(file_paths))
        backups: list[str] = []
        tracked: list[str] = []
        untracked: list[str] = []
        for file_path in unique:
            candidate = root / file_path
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                raise ValueError(f"Cannot discard an unsafe path: {file_path}")
            listed = self._runner.run(
                ["ls-files", "-z", "--", file_path],
                cwd=root,
                env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
            )
            backups.append(self._backup_worktree_file(root, file_path))
            (tracked if listed.stdout else untracked).append(file_path)
        if tracked:
            self._runner.run(
                ["restore", "--worktree", "--", *tracked],
                cwd=root,
                env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
            )
        for file_path in untracked:
            candidate = root / file_path
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink(missing_ok=True)
        self.invalidate_repository(root)
        return backups

    def add_ignore_rule(
        self, repository_path: str | Path, file_path: str, *, rule_kind: str
    ) -> str:
        path = Path(file_path)
        if rule_kind == "filename":
            rule = path.name
        elif rule_kind == "extension":
            if not path.suffix:
                raise ValueError("This file has no extension to ignore.")
            rule = f"*{path.suffix}"
        elif rule_kind == "path":
            rule = "/" + path.as_posix().lstrip("/")
        else:
            raise ValueError("Unsupported ignore rule.")
        ignore = Path(repository_path) / ".gitignore"
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        lines = existing.splitlines()
        if rule not in lines:
            with ignore.open("a", encoding="utf-8") as output:
                if existing and not existing.endswith("\n"):
                    output.write("\n")
                output.write(rule + "\n")
        self.invalidate_repository(repository_path)
        return rule

    def stop_lfs_tracking(self, repository_path: str | Path, file_path: str) -> None:
        check = self._runner.run(
            ["check-attr", "filter", "--", file_path],
            cwd=repository_path,
            env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
        )
        if not check.stdout.rstrip().endswith(": lfs"):
            raise ValueError("The selected file is not tracked by Git LFS.")
        try:
            listing = self._runner.run(["lfs", "track"], cwd=repository_path).stdout
            patterns = []
            for line in listing.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("Listing ") or " (" not in stripped:
                    continue
                pattern = stripped.rsplit(" (", 1)[0].strip().strip('"')
                candidate = Path(file_path)
                if candidate.match(pattern) or candidate.match(f"**/{pattern}"):
                    patterns.append(pattern)
            if not patterns:
                raise ValueError("Git LFS did not report the active tracking rule.")
            self._runner.run(["lfs", "untrack", patterns[-1]], cwd=repository_path)
        except (FileNotFoundError, GitCommandFailure) as error:
            raise ValueError(
                "Git LFS is unavailable. Install Git LFS and run `git lfs install` first."
            ) from error
        self.invalidate_repository(repository_path)

    def _staged_paths(self, repository_path: str | Path, file_path: str) -> list[str]:
        """A staged rename belongs to both paths when reviewing or unstaging it."""
        raw = self._runner.run(
            ["status", "--porcelain", "-z", "--untracked-files=no"], cwd=repository_path
        ).stdout
        records = iter(raw.split("\0"))
        for record in records:
            if len(record) < 4:
                continue
            xy, destination = record[:2], record[3:]
            if "R" in xy or "C" in xy:
                source = next(records, "")
                if xy[0] == "R" and destination == file_path and source:
                    return [source, destination]
        return [file_path]

    def get_diff(
        self,
        repository_path: str | Path,
        file_path: str,
        *,
        staged: bool = False,
        force_refresh: bool = False,
    ) -> list[DiffHunk]:
        return self.get_preview(
            repository_path, file_path, staged=staged, force_refresh=force_refresh
        ).hunks

    def get_preview(
        self,
        repository_path: str | Path,
        file_path: str,
        *,
        staged: bool = False,
        force_refresh: bool = False,
    ) -> DiffPreview:
        key = (str(Path(repository_path)), file_path, staged)
        cached = self._diff_cache.get(key)
        if not force_refresh and cached and cached[0] > self._monotonic():
            return cached[1]
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
        if staged:
            args.append("--staged")
        paths = self._staged_paths(repository_path, file_path) if staged else [file_path]
        args.extend(["--", *paths])
        try:
            raw = self._runner.run(
                args,
                cwd=repository_path,
                preserve_newlines=True,
                env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
            ).stdout
            if not staged and not raw:
                tracked = self._runner.run(
                    ["ls-files", "-z", "--", file_path],
                    cwd=repository_path,
                    env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
                ).stdout
                path = Path(repository_path) / file_path
                if not tracked and (path.is_file() or path.is_symlink()):
                    try:
                        raw = self._runner.run(
                            [
                                "diff",
                                "--no-index",
                                "--no-ext-diff",
                                "--no-textconv",
                                "--no-color",
                                "--",
                                os.devnull,
                                file_path,
                            ],
                            cwd=repository_path,
                            preserve_newlines=True,
                        ).stdout
                    except GitCommandFailure as exc:
                        if exc.result.exit_code != 1:
                            raise
                        raw = exc.result.stdout
            preview = self._parse_preview(raw)
        except GitOutputLimitError as error:
            preview = DiffPreview([], str(error))
        except UnicodeDecodeError:
            preview = DiffPreview(
                [],
                "This file is not UTF-8 text. Use an external viewer; "
                "whole-file staging is available.",
            )
        if len(self._diff_cache) >= 16:
            self._diff_cache.pop(next(iter(self._diff_cache)))
        self._diff_cache[key] = (self._monotonic() + self._cache_ttl_seconds, preview)
        return preview

    @staticmethod
    def _parse_preview(raw: str) -> DiffPreview:
        if len(raw) > 2_000_000 or raw.count("\n") > 10000:
            return DiffPreview(
                [],
                "This diff exceeds the desktop preview limit. Use an external viewer; whole-file"
                " staging remains available.",
            )
        hunks = parse_unified_diff(raw)
        metadata = raw.split("\n@@", 1)[0]
        message = ""
        supported = bool(hunks)
        if "Binary files " in metadata or "GIT binary patch" in metadata:
            message = (
                "Binary file changed. Use an external viewer; whole-file staging is available."
            )
            supported = False
        elif any(line.endswith((" 120000", " 160000")) for line in metadata.splitlines()):
            message = (
                "Symlink or submodule change. Review the target below; use whole-file staging."
            )
            supported = False
        elif "rename from " in metadata or "copy from " in metadata:
            message = "Renamed or copied file. Use whole-file staging for this change."
            supported = False
        elif metadata.startswith(("diff --cc ", "diff --combined ")):
            message = "Unresolved conflict. Resolve the file, then stage it to mark it resolved."
            supported = False
        elif not hunks:
            if "new file mode " in metadata:
                message = "New empty file. Use Stage Selected to include it in the commit."
            elif "deleted file mode " in metadata:
                message = "Empty file deleted. Use Stage Selected to record its removal."
            elif raw:
                message = "File metadata changed (mode or path); no text hunks to display."
            else:
                message = "No changes on this side of the file. Refresh if it changed externally."
        mode = "100755" if "file mode 100755" in metadata else "100644"
        return DiffPreview(
            hunks,
            message,
            supported,
            raw,
            "new file mode " in metadata,
            "deleted file mode " in metadata,
            mode,
        )

    def stage_hunk(self, repository_path: str | Path, file_path: str, hunk: DiffHunk) -> None:
        preview = self.get_preview(repository_path, file_path, force_refresh=True)
        if hunk not in preview.hunks:
            raise ValueError("File changed. Refresh the diff before staging.")
        self.apply_hunk(
            repository_path, file_path, preview, preview.hunks.index(hunk), action="stage"
        )

    def apply_hunk(
        self,
        repository_path: str | Path,
        file_path: str,
        preview: DiffPreview,
        hunk_index: int,
        *,
        action: str,
        selected_lines: set[int] | None = None,
    ) -> str | None:
        if action not in {"stage", "unstage", "discard"}:
            raise ValueError("Unsupported diff action.")
        current = self.get_preview(
            repository_path, file_path, staged=action == "unstage", force_refresh=True
        )
        if current != preview:
            raise ValueError(
                "File changed since this preview. Refresh and review it before retrying."
            )
        if not current.partial_supported or not 0 <= hunk_index < len(current.hunks):
            raise ValueError(
                "Partial changes are unavailable for this file. Use whole-file staging."
            )
        hunk = current.hunks[hunk_index]
        changed = {
            i
            for i, line in enumerate(hunk.lines)
            if line.type in {DiffLineType.ADDITION, DiffLineType.DELETION}
        }
        if selected_lines is not None and (not selected_lines or not selected_lines <= changed):
            raise ValueError("Select added or deleted lines from one hunk.")
        whole_file = len(current.hunks) == 1 and (
            selected_lines is None or selected_lines == changed
        )
        if whole_file and action == "unstage" and current.new_file:
            self.unstage_file(repository_path, file_path)
            return None
        if whole_file and action == "stage" and current.deleted_file:
            self.stage_file(repository_path, file_path)
            return None
        reverse = action != "stage"
        patch = build_patch_for_hunk(
            file_path,
            hunk,
            selected_lines=selected_lines,
            reverse=reverse,
            new_file=(current.deleted_file if reverse else current.new_file),
            mode=current.mode,
        )
        args = ["apply", "--whitespace=nowarn"]
        if action != "discard":
            args.append("--cached")
        # Allow patches for empty/new files; still validate the exact source context.
        args.append("--unidiff-zero")
        self._runner.run(
            [*args, "--check", "-"], cwd=repository_path, input_text=patch, preserve_newlines=True
        )
        backup = (
            self._backup_worktree_file(repository_path, file_path) if action == "discard" else None
        )
        self._runner.run(
            [*args, "-"], cwd=repository_path, input_text=patch, preserve_newlines=True
        )
        self.invalidate_repository(repository_path)
        return backup

    def _backup_worktree_file(self, repository_path: str | Path, file_path: str) -> str:
        source = Path(repository_path) / file_path
        if source.is_symlink():
            raise ValueError("Partial discard is unavailable for symbolic links.")
        git_dir = Path(
            self._runner.run(
                ["rev-parse", "--absolute-git-dir"], cwd=repository_path
            ).stdout.strip()
        )
        folder = git_dir / "gitqt-discard-backups"
        folder.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="discard-", dir=folder))
        exists = source.exists()
        if exists:
            (backup / "content").write_bytes(source.read_bytes())
        (backup / "info.json").write_text(
            json.dumps(
                {
                    "repository": str(Path(repository_path).resolve()),
                    "path": file_path,
                    "existed": exists,
                    "mode": source.stat().st_mode if exists else None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(backup)

    def invalidate_repository(self, repository_path: str | Path) -> None:
        key = str(Path(repository_path))
        self._working_tree_cache.pop(key, None)
        for cache_key in list(self._diff_cache):
            if cache_key[0] == key:
                self._diff_cache.pop(cache_key, None)
