from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gitqt.parsers.diff import DiffHunk, parse_unified_diff
from gitqt.parsers.history import HistoryGraphRow, parse_history_graph
from gitqt.services.caching import TtlCache, repository_cache_key
from gitqt.services.git import GitCommandFailure, GitRunner


@dataclass(slots=True)
class CommitFileChange:
    """A single file changed by a commit."""

    status: str  # 'A', 'M', 'D', 'R', 'C', 'T', 'U'
    path: str
    old_path: str | None = None


class HistoryService:
    """Load commit history rows with lightweight graph projection metadata."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        cache_ttl_seconds: float = 5.0,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._cache: TtlCache[
            tuple[str, int, bool, tuple[str, ...], int], list[HistoryGraphRow]
        ] = TtlCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=16,
            monotonic_func=monotonic_func,
        )
        self._files_cache: TtlCache[tuple[str, str], list[CommitFileChange]] = TtlCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=16,
            monotonic_func=monotonic_func,
        )
        self._file_diff_cache: TtlCache[tuple[str, str, str], list[DiffHunk]] = TtlCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=16,
            monotonic_func=monotonic_func,
        )

    def get_graph(
        self,
        repository_path: str | Path,
        *,
        limit: int = 100,
        all_refs: bool = True,
        ref: str | None = None,
        refs: Sequence[str] | None = None,
        force_refresh: bool = False,
        skip: int = 0,
    ) -> list[HistoryGraphRow]:
        normalized_limit = max(1, limit)
        ref_list = self._normalize_refs(ref, refs)
        effective_all_refs = bool(all_refs) and not ref_list
        cache_key = (
            repository_cache_key(repository_path),
            normalized_limit,
            effective_all_refs,
            tuple(ref_list),
            skip,
        )
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        args = [
            "log",
            "--graph",
            "--decorate=short",
            "--date=short",
            f"--max-count={normalized_limit}",
            f"--skip={max(0, skip)}",
            "--pretty=format:%h%x1f%an%x1f%ad%x1f%s%x1f%D%x1f%p%x1f%ae%x1f%cr%x1f%H",
        ]
        if ref_list:
            args.extend(ref_list)
        elif all_refs:
            args.extend(["--exclude=refs/gitqt/*", "--all"])
        result = self._runner.run(args, cwd=repository_path)
        rows = parse_history_graph(result.stdout)
        self._cache.set(cache_key, rows)
        return rows

    def get_tips(self, repository_path, refs=None):
        if refs:
            return tuple(
                self._runner.run(
                    ["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repository_path
                ).stdout.strip()
                for ref in refs
            )
        raw = self._runner.run(
            [
                "for-each-ref",
                "--format=%(objecttype) %(objectname) %(*objecttype) %(*objectname)",
                "refs/heads/",
                "refs/remotes/",
                "refs/tags/",
            ],
            cwd=repository_path,
        ).stdout
        tips = set()
        for line in raw.splitlines():
            fields = line.split()
            if fields[0] == "commit":
                tips.add(fields[1])
            elif len(fields) == 4 and fields[2] == "commit":
                tips.add(fields[3])
            elif len(fields) == 4 and fields[2] == "tag":
                try:
                    tips.add(
                        self._runner.run(
                            ["rev-parse", "--verify", f"{fields[1]}^{{commit}}"],
                            cwd=repository_path,
                        ).stdout.strip()
                    )
                except GitCommandFailure:
                    pass  # Tags can legitimately point to blobs or trees.
        try:
            tips.add(
                self._runner.run(
                    ["rev-parse", "--verify", "HEAD"], cwd=repository_path
                ).stdout.strip()
            )
        except GitCommandFailure:
            pass  # An unborn branch legitimately has no HEAD.
        return tuple(sorted(tips))

    def get_commit_files(
        self,
        repository_path: str | Path,
        commit_hash: str,
        *,
        force_refresh: bool = False,
    ) -> list[CommitFileChange]:
        """Return the list of files changed by ``commit_hash``."""

        cache_key = (repository_cache_key(repository_path), commit_hash)
        if not force_refresh:
            cached = self._files_cache.get(cache_key)
            if cached is not None:
                return cached
        result = self._runner.run(
            [
                "log",
                "--max-count=1",
                "--no-walk",
                "--root",
                "--format=",
                "--name-status",
                "-z",
                "-r",
                "-M",
                "-C",
                "--first-parent",
                "-m",
                commit_hash,
            ],
            cwd=repository_path,
            preserve_newlines=True,
        )
        changes = _parse_name_status(result.stdout)
        self._files_cache.set(cache_key, changes)
        return changes

    def get_commit_file_diff(
        self,
        repository_path: str | Path,
        commit_hash: str,
        file_path: str,
        *,
        force_refresh: bool = False,
    ) -> list[DiffHunk]:
        """Return unified diff hunks for a single file inside ``commit_hash``."""

        cache_key = (
            repository_cache_key(repository_path),
            commit_hash,
            file_path,
        )
        if not force_refresh:
            cached = self._file_diff_cache.get(cache_key)
            if cached is not None:
                return cached
        result = self._runner.run(
            [
                "log",
                "--max-count=1",
                "--no-walk",
                "--root",
                "--format=",
                "--patch",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--first-parent",
                "-m",
                "-M",
                "-C",
                commit_hash,
                "--",
                file_path,
            ],
            cwd=repository_path,
            preserve_newlines=True,
            env_overrides={"GIT_LITERAL_PATHSPECS": "1"},
        )
        hunks = parse_unified_diff(result.stdout)
        self._file_diff_cache.set(cache_key, hunks)
        return hunks

    def invalidate_repository(self, repository_path: str | Path) -> None:
        key_prefix = repository_cache_key(repository_path)
        self._cache.invalidate_matching(lambda k: k[0] == key_prefix)
        self._files_cache.invalidate_matching(lambda k: k[0] == key_prefix)
        self._file_diff_cache.invalidate_matching(lambda k: k[0] == key_prefix)

    @staticmethod
    def _normalize_refs(
        ref: str | None,
        refs: Sequence[str] | None,
    ) -> list[str]:
        collected: list[str] = []
        seen: set[str] = set()
        if ref is not None:
            candidate = ref.strip()
            if candidate and candidate not in seen:
                collected.append(candidate)
                seen.add(candidate)
        if refs is not None:
            for raw in refs:
                if raw is None:
                    continue
                candidate = str(raw).strip()
                if candidate and candidate not in seen:
                    collected.append(candidate)
                    seen.add(candidate)
        return sorted(collected)


def _parse_name_status(stdout: str) -> list[CommitFileChange]:
    if "\0" in stdout:
        tokens = iter(stdout.split("\0"))
        changes = []
        for status in tokens:
            if not status:
                continue
            first = next(tokens, None)
            if first is None:
                raise ValueError("Incomplete Git file list.")
            old_path = None
            if status[0] in {"R", "C"}:
                old_path, first = first, next(tokens, None)
                if first is None:
                    raise ValueError("Incomplete rename in Git file list.")
            changes.append(CommitFileChange(status[0], first, old_path))
        return changes
    changes: list[CommitFileChange] = []
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status_token = parts[0].strip()
        if not status_token:
            continue
        primary_status = status_token[0]
        if primary_status in ("R", "C") and len(parts) >= 3:
            old_path = parts[1]
            new_path = parts[2]
            changes.append(
                CommitFileChange(
                    status=primary_status,
                    path=new_path,
                    old_path=old_path,
                )
            )
        else:
            changes.append(
                CommitFileChange(
                    status=primary_status,
                    path=parts[1],
                )
            )
    return changes
