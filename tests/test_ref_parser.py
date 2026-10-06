from __future__ import annotations

from gitqt.parsers.refs import (
    parse_ahead_behind_counts,
    parse_local_branch_refs,
    parse_remote_branch_refs,
)


def test_parse_local_branch_refs_maps_head_and_upstream() -> None:
    stdout = "\n".join(
        [
            "main|origin/main|*|abc1234",
            "feature/history|| |def5678",
        ]
    )

    branches = parse_local_branch_refs(stdout)

    assert len(branches) == 2
    main, feature = branches
    assert main.name == "main"
    assert main.is_head
    assert main.upstream == "origin/main"
    assert main.commit_short_hash == "abc1234"
    assert feature.name == "feature/history"
    assert not feature.is_head
    assert feature.upstream is None


def test_parse_remote_branch_refs_maps_name_and_hash() -> None:
    stdout = "\n".join(["origin/main|abc1234", "origin/feature/history|def5678"])

    branches = parse_remote_branch_refs(stdout)

    assert [branch.name for branch in branches] == ["origin/main", "origin/feature/history"]
    assert [branch.commit_short_hash for branch in branches] == ["abc1234", "def5678"]


def test_parse_ahead_behind_counts_converts_left_right_to_ahead_behind() -> None:
    ahead, behind = parse_ahead_behind_counts("2\t5\n")

    assert ahead == 5
    assert behind == 2
