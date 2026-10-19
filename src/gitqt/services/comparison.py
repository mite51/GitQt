"""Read-only, resolved-endpoint comparisons with literal paths and bounded previews."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitqt.parsers.diff import DiffHunk, parse_unified_diff
from gitqt.services.git import GitOutputLimitError, GitRunner
from gitqt.services.history import CommitFileChange, _parse_name_status


@dataclass(frozen=True, slots=True)
class ComparisonRequest:
    base: str = "HEAD"
    target: str = "HEAD"
    target_kind: str = "commit"  # commit, index, worktree
    merge_base: bool = False
    parent: int | None = None  # 1-based; 0 means the empty tree for a root commit


@dataclass(frozen=True, slots=True)
class CommitDetails:
    oid: str
    parents: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class Comparison:
    arguments: tuple[str, ...]
    description: str
    files: tuple[CommitFileChange, ...]
    commit: CommitDetails | None
    state: tuple[tuple[str, tuple[int, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class DiffOptions:
    context: int = 3
    whitespace: str = "exact"  # exact, space-change, all-space


@dataclass(slots=True)
class ComparisonDiff:
    path: str
    raw: str
    hunks: list[DiffHunk]
    notice: str = ""


class ComparisonService:
    def __init__(self, runner: GitRunner) -> None:
        self._runner = runner

    def references(self, repository_path: str | Path) -> list[str]:
        """Return full local, remote, and tag names without an arbitrary row cap."""
        raw = self._runner.run(
            ["for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/", "refs/tags/"],
            cwd=repository_path,
        ).stdout
        return ["HEAD", *raw.splitlines()]

    def _resolve(self, repository_path: str | Path, ref: str) -> str:
        if not ref.strip():
            raise ValueError("Choose a commit or reference.")
        return self._runner.run(
            ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            cwd=repository_path,
        ).stdout.strip()

    def commit_details(self, repository_path: str | Path, ref: str) -> CommitDetails:
        oid = self._resolve(repository_path, ref)
        raw = self._runner.run(
            ["show", "-s", "--no-show-signature", "--format=%P%x00%B", oid],
            cwd=repository_path,
            preserve_newlines=True,
        ).stdout
        parents, message = raw.split("\0", 1)
        return CommitDetails(oid, tuple(parents.split()), message.rstrip("\n"))

    def compare(self, repository_path: str | Path, request: ComparisonRequest) -> Comparison:
        if request.target_kind not in {"commit", "index", "worktree"}:
            raise ValueError("Unknown comparison target.")
        if request.merge_base and request.target_kind != "commit":
            raise ValueError("Merge-base comparison requires two commits or references.")
        if request.parent is not None and request.target_kind != "commit":
            raise ValueError("Parent comparison requires a commit target.")
        commit = (
            self.commit_details(repository_path, request.target)
            if request.target_kind == "commit"
            else None
        )
        if request.parent is not None:
            assert commit is not None
            if request.parent == 0 and not commit.parents:
                base = self._runner.run(
                    ["hash-object", "-t", "tree", "--stdin"],
                    cwd=repository_path,
                    input_text="",
                ).stdout.strip()
                base_label = "empty tree (root commit)"
            elif 1 <= request.parent <= len(commit.parents):
                base = commit.parents[request.parent - 1]
                base_label = f"parent {request.parent} ({base[:12]})"
            else:
                raise ValueError("That parent does not exist for this commit.")
        elif request.base == "INDEX" and request.target_kind == "worktree":
            base = ""
            base_label = "index"
        else:
            base = self._resolve(repository_path, request.base)
            base_label = f"{request.base} ({base[:12]})"
        if request.merge_base:
            assert commit is not None
            base = self._runner.run(
                ["merge-base", base, commit.oid], cwd=repository_path
            ).stdout.strip()
            base_label = f"merge base ({base[:12]}) of {request.base} and {request.target}"
        if request.target_kind == "commit":
            assert commit is not None
            arguments = (base, commit.oid)
            target_label = f"{request.target} ({commit.oid[:12]})"
        elif request.target_kind == "index":
            arguments = ("--cached", base)
            target_label = "staged snapshot (index)"
        else:
            arguments = (base,) if base else ()
            target_label = "working tree (tracked files; untracked files are excluded)"
        index_state = ()
        if request.target_kind != "commit":
            index_path = self._runner.run(
                ["rev-parse", "--git-path", "index"], cwd=repository_path
            ).stdout.strip()
            index_path = str(Path(repository_path) / index_path)
            index_state = ((index_path, self._stat(index_path)),)
        list_args = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            "-M",
            *arguments,
            "--",
        ]
        raw = self._runner.run(
            list_args,
            cwd=repository_path,
            preserve_newlines=True,
        ).stdout
        files = tuple(_parse_name_status(raw))
        state = index_state
        if request.target_kind == "worktree":
            paths = [str(Path(repository_path) / change.path) for change in files]
            paths.extend(
                str(Path(repository_path) / change.old_path) for change in files if change.old_path
            )
            state += tuple((path, self._stat(path)) for path in paths)
            # Validate the changed-file set on both sides of the stat capture.
            # This prevents a concurrent edit from silently replacing an entry.
            after = self._runner.run(
                list_args,
                cwd=repository_path,
                preserve_newlines=True,
            ).stdout
            if raw != after:
                raise ValueError(
                    "The working tree changed during comparison. Press Compare to refresh."
                )
        comparison = Comparison(
            arguments,
            f"{base_label} → {target_label}. Additions are on the right / target side.",
            files,
            commit,
            state,
        )
        self._check_state(comparison)
        return comparison

    def file_diff(
        self,
        repository_path: str | Path,
        comparison: Comparison,
        change: CommitFileChange,
        options: DiffOptions | None = None,
    ) -> ComparisonDiff:
        self._check_state(comparison)
        options = options or DiffOptions()
        flags = {
            "exact": [],
            "space-change": ["--ignore-space-change"],
            "all-space": ["--ignore-all-space"],
        }
        if options.whitespace not in flags:
            raise ValueError("Unknown whitespace option.")
        paths = [change.path]
        if change.old_path and change.old_path != change.path:
            paths.insert(0, change.old_path)
        args = [
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "-M",
            f"--unified={min(1000, max(0, options.context))}",
            *flags[options.whitespace],
            *comparison.arguments,
            "--",
            *paths,
        ]
        try:
            raw = self._runner.run(
                args,
                cwd=repository_path,
                preserve_newlines=True,
                env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
            ).stdout
        except (GitOutputLimitError, UnicodeDecodeError) as exc:
            reason = (
                "Diff exceeds the 2 MB preview limit."
                if isinstance(exc, GitOutputLimitError)
                else "The file uses a text encoding that cannot be previewed as UTF-8."
            )
            return ComparisonDiff(change.path, "", [], reason + " Use an external viewer.")
        self._check_state(comparison)
        hunks = parse_unified_diff(raw)
        if sum(len(hunk.lines) for hunk in hunks) > 10_000:
            return ComparisonDiff(
                change.path,
                "",
                [],
                "Diff exceeds the 10,000-line preview limit. Use an external viewer.",
            )
        notice = ""
        if len(raw) > 250_000:
            notice = (
                "Large diff: word and syntax highlighting are disabled "
                "to keep the preview responsive."
            )
        if "Binary files " in raw or "GIT binary patch" in raw:
            notice = (
                "Binary/image preview unavailable. Metadata is shown below; "
                "use an external viewer for the file contents."
            )
        elif not hunks:
            notice = "No text changes under these display options; file metadata is shown below."
        return ComparisonDiff(change.path, raw, hunks, notice)

    @staticmethod
    def _stat(path: str) -> tuple[int, ...]:
        try:
            stat = Path(path).lstat()
            return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        except FileNotFoundError:
            return ()

    @classmethod
    def _check_state(cls, comparison: Comparison) -> None:
        if any(cls._stat(path) != state for path, state in comparison.state):
            raise ValueError(
                "The index or working tree changed. Press Compare to refresh this comparison."
            )
