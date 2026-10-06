from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LocalBranchRef:
    name: str
    upstream: str | None
    is_head: bool
    commit_short_hash: str | None


@dataclass(slots=True)
class RemoteBranchRef:
    name: str
    commit_short_hash: str | None


def parse_local_branch_refs(stdout: str) -> list[LocalBranchRef]:
    branches: list[LocalBranchRef] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 4:
            continue
        name, upstream, head_marker, commit_short_hash = parts
        branches.append(
            LocalBranchRef(
                name=name.strip(),
                upstream=upstream.strip() or None,
                is_head=head_marker.strip() == "*",
                commit_short_hash=commit_short_hash.strip() or None,
            )
        )
    return branches


def parse_remote_branch_refs(stdout: str) -> list[RemoteBranchRef]:
    branches: list[RemoteBranchRef] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 2:
            continue
        name, commit_short_hash = parts
        branches.append(
            RemoteBranchRef(
                name=name.strip(),
                commit_short_hash=commit_short_hash.strip() or None,
            )
        )
    return branches


def parse_ahead_behind_counts(stdout: str) -> tuple[int, int]:
    fields = stdout.split()
    if len(fields) != 2:
        return 0, 0
    behind_raw, ahead_raw = fields
    try:
        behind = int(behind_raw)
        ahead = int(ahead_raw)
    except ValueError:
        return 0, 0
    return ahead, behind
