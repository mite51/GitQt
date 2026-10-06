from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gitqt.services.caching import TtlCache, repository_cache_key
from gitqt.services.git import GitCommandFailure, GitRunner


@dataclass(slots=True)
class CommitAuthor:
    name: str
    email: str


@dataclass(slots=True)
class CommitValidationResult:
    is_valid: bool
    errors: list[str]
    summary: str
    body: str


@dataclass(slots=True)
class CommitExecutionResult:
    success: bool
    message: str
    commit_hash: str | None = None


@dataclass(frozen=True)
class AmendPreview:
    oid: str
    summary: str
    body: str
    published_refs: tuple[str, ...]


class CommitService:
    """Validate commit content and execute commits for a repository."""

    def __init__(
        self,
        runner: GitRunner,
        *,
        author_cache_ttl_seconds: float = 300.0,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._author_cache: TtlCache[str, CommitAuthor] = TtlCache(
            ttl_seconds=author_cache_ttl_seconds,
            monotonic_func=monotonic_func,
        )

    def get_author(
        self,
        repository_path: str | Path,
        *,
        force_refresh: bool = False,
    ) -> CommitAuthor:
        cache_key = repository_cache_key(repository_path)
        if not force_refresh:
            cached = self._author_cache.get(cache_key)
            if cached is not None:
                return cached
        author = self._load_author(repository_path)
        self._author_cache.set(cache_key, author)
        return author

    def invalidate_repository(self, repository_path: str | Path) -> None:
        self._author_cache.invalidate(repository_cache_key(repository_path))

    def _load_author(self, repository_path: str | Path) -> CommitAuthor:
        """Fetch user.name and user.email in a single git subprocess call."""
        try:
            result = self._runner.run(
                ["config", "--get-regexp", r"^user\.(name|email)$"],
                cwd=repository_path,
            )
        except GitCommandFailure:
            return CommitAuthor(name="", email="")

        name = ""
        email = ""
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            key, sep, value = line.partition(" ")
            if not sep:
                continue
            if key == "user.name":
                name = value.strip()
            elif key == "user.email":
                email = value.strip()
        return CommitAuthor(name=name, email=email)

    def validate_message(self, summary: str, body: str = "") -> CommitValidationResult:
        normalized_summary = summary.strip()
        normalized_body = body.strip()
        errors: list[str] = []

        if not normalized_summary:
            errors.append("Commit summary is required.")
        if "\n" in normalized_summary:
            errors.append("Commit summary must be a single line.")
        if len(normalized_summary) > 72:
            errors.append("Commit summary must be 72 characters or fewer.")

        return CommitValidationResult(
            is_valid=not errors,
            errors=errors,
            summary=normalized_summary,
            body=normalized_body,
        )

    def has_staged_changes(self, repository_path: str | Path) -> bool:
        result = self._runner.run(["diff", "--cached", "--name-only"], cwd=repository_path)
        return bool(result.stdout.strip())

    def prepare_amend(self, repository_path):
        oid = self._runner.run(
            ["rev-parse", "--verify", "HEAD"], cwd=repository_path
        ).stdout.strip()
        message = self._runner.run(
            ["show", "-s", "--format=%B", oid], cwd=repository_path
        ).stdout.rstrip("\n")
        summary, _, body = message.partition("\n")
        refs = self._runner.run(
            ["for-each-ref", f"--contains={oid}", "--format=%(refname:short)", "refs/remotes/"],
            cwd=repository_path,
        ).stdout
        return AmendPreview(oid, summary, body.lstrip("\n"), tuple(refs.splitlines()))

    def commit(
        self,
        repository_path: str | Path,
        *,
        summary: str,
        body: str = "",
        allow_empty: bool = False,
        amend: bool = False,
        signoff: bool = False,
        expected_head: str | None = None,
    ) -> CommitExecutionResult:
        validation = self.validate_message(summary, body)
        if not validation.is_valid:
            return CommitExecutionResult(success=False, message=" ".join(validation.errors))

        staged = self.has_staged_changes(repository_path)
        if amend:
            try:
                head = self._runner.run(
                    ["rev-parse", "--verify", "HEAD"], cwd=repository_path
                ).stdout.strip()
            except GitCommandFailure:
                return CommitExecutionResult(False, "There is no commit to amend yet.")
            if expected_head is not None and head != expected_head:
                return CommitExecutionResult(
                    False,
                    "HEAD changed since the amend preview. Turn Amend off and on to reload it.",
                )
            from gitqt.services.recovery import RecoveryService

            RecoveryService(self._runner).retain(repository_path, head, "before-amend")
        if not allow_empty and not amend and not staged:
            return CommitExecutionResult(
                success=False,
                message="No staged changes to commit. Stage changes or allow an empty commit.",
            )

        command = ["commit", "-m", validation.summary]
        if validation.body:
            command.extend(["-m", validation.body])
        if amend:
            command.append("--amend")
        if signoff:
            command.append("--signoff")
        if allow_empty or (amend and not staged):
            command.append("--allow-empty")

        try:
            self._runner.run(command, cwd=repository_path)
            head = self._runner.run(["rev-parse", "--short", "HEAD"], cwd=repository_path)
        except GitCommandFailure as exc:
            stderr = exc.result.stderr.strip()
            return CommitExecutionResult(
                success=False,
                message=stderr or "Git rejected the commit operation.",
            )

        commit_hash = head.stdout.strip() or None
        return CommitExecutionResult(
            success=True,
            message="Commit created successfully.",
            commit_hash=commit_hash,
        )
