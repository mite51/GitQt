from __future__ import annotations

import hashlib
from pathlib import Path

from gitqt.services.conflict import ConflictService
from gitqt.services.git import GitRunner


def repository_fingerprint(runner: GitRunner, repository: str) -> str:
    """Poll in a worker, including linked-worktree state and unstaged content changes."""
    status = runner.run(
        ["status", "--porcelain", "-z", "--untracked-files=all"], cwd=repository
    ).stdout
    refs = runner.run(["for-each-ref", "--format=%(refname) %(objectname)"], cwd=repository).stdout
    head = runner.run(["rev-parse", "--git-path", "HEAD"], cwd=repository).stdout.strip()
    head_path = Path(head)
    if not head_path.is_absolute():
        head_path = Path(repository) / head_path
    parts = [status, refs, head_path.read_text() if head_path.exists() else ""]
    # Status alone is unchanged while a file continues to be edited.
    from gitqt.parsers.status import parse_porcelain_status

    for entry in parse_porcelain_status(status):
        path = Path(repository) / entry.path
        try:
            info = path.lstat()
            parts.append(f"{entry.path}:{info.st_mtime_ns}:{info.st_size}")
        except FileNotFoundError:
            pass
    for metadata in ("index", "config", "config.worktree"):
        raw = runner.run(["rev-parse", "--git-path", metadata], cwd=repository).stdout.strip()
        path = Path(raw)
        if not path.is_absolute():
            path = Path(repository) / path
        try:
            info = path.stat()
            parts.append(f"{metadata}:{info.st_mtime_ns}:{info.st_size}")
        except FileNotFoundError:
            pass
    parts.append(str(ConflictService(runner)._detect_operation(repository)))
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()
