from __future__ import annotations

import re
from dataclasses import dataclass, field

_SHORT_HASH_PATTERN = re.compile(r"[0-9a-f]{7,40}$")


@dataclass(slots=True)
class HistoryGraphRow:
    graph_prefix: str
    commit_short_hash: str
    author: str
    date: str
    summary: str
    refs: tuple[str, ...]
    head_ref: str | None
    parents: tuple[str, ...] = field(default_factory=tuple)
    author_email: str = ""
    age: str = ""
    commit_full_hash: str = ""


def parse_history_graph(stdout: str) -> list[HistoryGraphRow]:
    rows: list[HistoryGraphRow] = []
    for raw_line in stdout.splitlines():
        if "\x1f" not in raw_line:
            continue
        fields = raw_line.split("\x1f")
        if len(fields) < 5:
            continue
        graph_and_hash = fields[0]
        author = fields[1]
        date = fields[2]
        summary = fields[3]
        decorations = fields[4]
        parents_raw = fields[5] if len(fields) >= 6 else ""
        author_email = fields[6] if len(fields) >= 7 else ""
        age_raw = fields[7] if len(fields) >= 8 else ""
        full_hash = fields[8] if len(fields) >= 9 else ""
        graph_prefix, commit_short_hash = _parse_graph_and_hash(graph_and_hash)
        if commit_short_hash is None:
            continue
        refs, head_ref = _parse_refs(decorations)
        parents = tuple(token for token in parents_raw.strip().split() if token)
        rows.append(
            HistoryGraphRow(
                graph_prefix=graph_prefix,
                commit_short_hash=commit_short_hash,
                author=author.strip(),
                date=date.strip(),
                summary=summary.strip(),
                refs=refs,
                head_ref=head_ref,
                parents=parents,
                author_email=author_email.strip(),
                age=age_raw.strip(),
                commit_full_hash=full_hash.strip(),
            )
        )
    return rows


def _parse_graph_and_hash(graph_and_hash: str) -> tuple[str, str | None]:
    normalized = graph_and_hash.rstrip()
    if not normalized:
        return "", None
    maybe_hash = normalized.split()[-1]
    if _SHORT_HASH_PATTERN.fullmatch(maybe_hash) is None:
        return normalized, None
    hash_start = normalized.rfind(maybe_hash)
    graph_prefix = normalized[:hash_start].rstrip()
    return graph_prefix, maybe_hash


def _parse_refs(decorations: str) -> tuple[tuple[str, ...], str | None]:
    raw_refs = [token.strip() for token in decorations.split(",") if token.strip()]
    refs: list[str] = []
    head_ref: str | None = None
    for raw_ref in raw_refs:
        if raw_ref.startswith("HEAD -> "):
            head_ref = raw_ref.removeprefix("HEAD -> ").strip() or None
            if head_ref is not None:
                refs.append(head_ref)
            continue
        # ``--decorate=short`` prefixes tag refs with ``tag: `` (e.g.
        # "tag: v1.2.3"). Strip that so the stored ref is a valid rev that
        # git log can resolve when used as a branch/tag filter.
        if raw_ref.startswith("tag: "):
            tag_name = raw_ref.removeprefix("tag: ").strip()
            if tag_name:
                refs.append(tag_name)
            continue
        refs.append(raw_ref)
    return tuple(refs), head_ref
