from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gitqt.services.caching import TtlCache, repository_cache_key
from gitqt.services.git import GitRunner


@dataclass(slots=True)
class TagInfo:
    name: str


@dataclass(slots=True)
class TagSnapshot:
    tags: list[TagInfo]


class TagService:
    """Manage lightweight and annotated tag creation."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        cache_ttl_seconds: float = 5.0,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._cache: TtlCache[str, TagSnapshot] = TtlCache(
            ttl_seconds=cache_ttl_seconds,
            monotonic_func=monotonic_func,
        )

    def list_tags(
        self,
        repository_path: str | Path,
        *,
        force_refresh: bool = False,
    ) -> TagSnapshot:
        cache_key = repository_cache_key(repository_path)
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        result = self._runner.run(
            ["tag", "--list", "--sort=-creatordate"],
            cwd=repository_path,
        )
        tags = [TagInfo(name=line.strip()) for line in result.stdout.splitlines() if line.strip()]
        snapshot = TagSnapshot(tags=tags)
        self._cache.set(cache_key, snapshot)
        return snapshot

    def invalidate_repository(self, repository_path: str | Path) -> None:
        self._cache.invalidate(repository_cache_key(repository_path))

    def create_tag(
        self,
        repository_path: str | Path,
        name: str,
        *,
        target: str | None = None,
        annotation: str | None = None,
    ) -> None:
        args = ["tag"]
        if annotation:
            args.extend(["-a", name, "-m", annotation])
        else:
            args.append(name)
        if target:
            args.append(target)
        self._runner.run(args, cwd=repository_path)
        self.invalidate_repository(repository_path)
