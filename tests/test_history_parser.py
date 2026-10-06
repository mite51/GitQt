from __future__ import annotations

from gitqt.parsers.history import parse_history_graph


def test_parse_history_graph_handles_linear_and_merge_rows() -> None:
    stdout = "\n".join(
        [
            "* abc1234\x1fAlice\x1f2026-04-13\x1fAdd dashboard state\x1fHEAD -> main, origin/main",
            "|\\",
            "| * def5678\x1fBob\x1f2026-04-12\x1fSpike branch layout\x1ffeature/layout",
            "|/",
            "*   fedcba9\x1fAlice\x1f2026-04-11\x1fMerge feature/layout\x1fmain",
        ]
    )

    rows = parse_history_graph(stdout)

    assert len(rows) == 3
    first = rows[0]
    assert first.graph_prefix == "*"
    assert first.commit_short_hash == "abc1234"
    assert first.head_ref == "main"
    assert first.refs == ("main", "origin/main")

    second = rows[1]
    assert second.graph_prefix == "| *"
    assert second.commit_short_hash == "def5678"
    assert second.refs == ("feature/layout",)

    third = rows[2]
    assert third.graph_prefix == "*"
    assert third.summary == "Merge feature/layout"


def test_parse_history_graph_extracts_parent_hashes_and_author_email() -> None:
    stdout = (
        "* abc1234\x1fAlice\x1f2026-04-13"
        "\x1fSpike topology\x1fHEAD -> main"
        "\x1fdef5678 fedcba9"
        "\x1falice@example.com"
    )

    rows = parse_history_graph(stdout)

    assert len(rows) == 1
    row = rows[0]
    assert row.parents == ("def5678", "fedcba9")
    assert row.author_email == "alice@example.com"


def test_parse_history_graph_extracts_age_and_full_hash() -> None:
    stdout = (
        "* abc1234\x1fAlice\x1f2026-04-13"
        "\x1fAdd age column\x1fHEAD -> main"
        "\x1fdef5678"
        "\x1falice@example.com"
        "\x1f2 days ago"
        "\x1fabc1234abcdef0123456789abcdef012345678"
    )

    rows = parse_history_graph(stdout)

    assert len(rows) == 1
    row = rows[0]
    assert row.age == "2 days ago"
    assert row.commit_full_hash == "abc1234abcdef0123456789abcdef012345678"


def test_parse_history_graph_age_defaults_to_empty_when_missing() -> None:
    stdout = "* abc1234\x1fAlice\x1f2026-04-13\x1fInitial commit\x1fHEAD -> main"

    rows = parse_history_graph(stdout)

    assert rows[0].age == ""
    assert rows[0].commit_full_hash == ""


def test_parse_history_graph_handles_missing_parents_field_as_empty_tuple() -> None:
    stdout = "* abc1234\x1fAlice\x1f2026-04-13\x1fInitial commit\x1fHEAD -> main"

    rows = parse_history_graph(stdout)

    assert len(rows) == 1
    assert rows[0].parents == ()
    assert rows[0].author_email == ""
