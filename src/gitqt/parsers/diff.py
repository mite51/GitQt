from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class DiffLineType(StrEnum):
    CONTEXT = "context"
    ADDITION = "addition"
    DELETION = "deletion"
    META = "meta"


@dataclass(slots=True)
class DiffLine:
    type: DiffLineType
    text: str
    old_line_number: int | None
    new_line_number: int | None


@dataclass(slots=True)
class DiffHunk:
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine]


def parse_unified_diff(stdout: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None
    old_line = 0
    new_line = 0

    for raw_line in stdout.removesuffix("\n").split("\n"):
        if raw_line.startswith("@@"):
            parsed = _parse_hunk_header(raw_line)
            if parsed is None:
                continue
            old_start, old_count, new_start, new_count = parsed
            current_hunk = DiffHunk(
                header=raw_line,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=[],
            )
            old_line = old_start
            new_line = new_start
            hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if raw_line.startswith("+"):
            current_hunk.lines.append(
                DiffLine(
                    type=DiffLineType.ADDITION,
                    text=raw_line[1:],
                    old_line_number=None,
                    new_line_number=new_line,
                )
            )
            new_line += 1
        elif raw_line.startswith("-"):
            current_hunk.lines.append(
                DiffLine(
                    type=DiffLineType.DELETION,
                    text=raw_line[1:],
                    old_line_number=old_line,
                    new_line_number=None,
                )
            )
            old_line += 1
        elif raw_line.startswith(" "):
            current_hunk.lines.append(
                DiffLine(
                    type=DiffLineType.CONTEXT,
                    text=raw_line[1:],
                    old_line_number=old_line,
                    new_line_number=new_line,
                )
            )
            old_line += 1
            new_line += 1
        else:
            current_hunk.lines.append(
                DiffLine(
                    type=DiffLineType.META,
                    text=raw_line,
                    old_line_number=None,
                    new_line_number=None,
                )
            )

    return hunks


def _parse_hunk_header(header: str) -> tuple[int, int, int, int] | None:
    match = HUNK_HEADER_PATTERN.match(header)
    if match is None:
        return None
    old_start = int(match.group("old_start"))
    old_count = int(match.group("old_count") or "1")
    new_start = int(match.group("new_start"))
    new_count = int(match.group("new_count") or "1")
    return old_start, old_count, new_start, new_count
