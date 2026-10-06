from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gitqt.services.caching import TtlCache, repository_cache_key
from gitqt.services.git import GitRunner


@dataclass(slots=True)
class StashEntry:
    ref: str
    description: str


@dataclass(slots=True)
class StashSnapshot:
    entries: list[StashEntry]


class StashService:
    """Manage stash workflows for a repository."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        cache_ttl_seconds: float = 5.0,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._cache: TtlCache[str, StashSnapshot] = TtlCache(
            ttl_seconds=cache_ttl_seconds,
            monotonic_func=monotonic_func,
        )

    def list_stashes(
        self,
        repository_path: str | Path,
        *,
        force_refresh: bool = False,
    ) -> StashSnapshot:
        cache_key = repository_cache_key(repository_path)
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        result = self._runner.run(
            ["stash", "list", "--format=%gd|%gs"],
            cwd=repository_path,
        )
        snapshot = StashSnapshot(entries=_parse_stash_output(result.stdout))
        self._cache.set(cache_key, snapshot)
        return snapshot

    def invalidate_repository(self, repository_path: str | Path) -> None:
        self._cache.invalidate(repository_cache_key(repository_path))

    def create_stash(
        self,
        repository_path: str | Path,
        *,
        message: str | None = None,
        include_untracked: bool = False,
    ) -> None:
        args = ["stash", "push"]
        if include_untracked:
            args.append("--include-untracked")
        if message:
            args.extend(["-m", message])
        self._runner.run(args, cwd=repository_path)
        self.invalidate_repository(repository_path)

    def apply_stash(self, repository_path: str | Path, stash_ref: str = "stash@{0}") -> None:
        self._runner.run(["stash", "apply", stash_ref], cwd=repository_path)
        self.invalidate_repository(repository_path)

    def pop_stash(self, repository_path: str | Path, stash_ref: str = "stash@{0}") -> None:
        self._runner.run(["stash", "pop", stash_ref], cwd=repository_path)
        self.invalidate_repository(repository_path)

    def drop_stash(self, repository_path: str | Path, stash_ref: str = "stash@{0}") -> None:
        self._runner.run(["stash", "drop", stash_ref], cwd=repository_path)
        self.invalidate_repository(repository_path)


def _parse_stash_output(raw: str) -> list[StashEntry]:
    entries: list[StashEntry] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        ref, separator, description = stripped.partition("|")
        if not separator:
            ref = stripped.split(":", maxsplit=1)[0].strip()
            description = stripped
        entries.append(StashEntry(ref=ref.strip(), description=description.strip()))
    return entries
