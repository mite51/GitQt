from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Porcelain XY combinations that indicate an active merge conflict.
# See: https://git-scm.com/docs/git-status#_short_format
_CONFLICT_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})


class WorkingTreeState(StrEnum):
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    UNTRACKED = "untracked"


@dataclass(slots=True)
class WorkingTreeEntry:
    path: str
    state: WorkingTreeState
    staged: bool
    unstaged: bool
    conflicted: bool = False
    lfs_tracked: bool = False


def parse_porcelain_status(stdout: str) -> list[WorkingTreeEntry]:
    entries: list[WorkingTreeEntry] = []
    if "\0" in stdout:
        records = iter(stdout.split("\0"))
        for record in records:
            if len(record) < 4:
                continue
            xy, path = record[:2], record[3:]
            if "R" in xy or "C" in xy:
                next(records, None)  # With -z, the destination precedes the original path.
            entries.append(
                WorkingTreeEntry(
                    path=path,
                    state=(
                        WorkingTreeState.UNTRACKED if xy == "??" else _resolve_state(xy[0], xy[1])
                    ),
                    staged=xy[0] not in {" ", "?"},
                    unstaged=xy == "??" or xy[1] not in {" ", "?"},
                    conflicted=xy in _CONFLICT_CODES,
                )
            )
        return entries
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue

        if line.startswith("?? "):
            entries.append(
                WorkingTreeEntry(
                    path=_normalize_path(line[3:]),
                    state=WorkingTreeState.UNTRACKED,
                    staged=False,
                    unstaged=True,
                )
            )
            continue

        if len(line) < 4:
            continue

        index_status = line[0]
        worktree_status = line[1]
        path = _normalize_path(line[3:])
        state = _resolve_state(index_status=index_status, worktree_status=worktree_status)
        conflicted = (index_status + worktree_status) in _CONFLICT_CODES
        entries.append(
            WorkingTreeEntry(
                path=path,
                state=state,
                staged=index_status not in {" ", "?"},
                unstaged=worktree_status not in {" ", "?"},
                conflicted=conflicted,
            )
        )
    return entries


def _resolve_state(index_status: str, worktree_status: str) -> WorkingTreeState:
    if "D" in (index_status, worktree_status):
        return WorkingTreeState.DELETED
    if "A" in (index_status, worktree_status):
        return WorkingTreeState.ADDED
    return WorkingTreeState.MODIFIED


def _normalize_path(path: str) -> str:
    if " -> " in path:
        _, destination = path.split(" -> ", maxsplit=1)
        path = destination
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path
