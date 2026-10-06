from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from gitqt.ui.avatars import color_for, initials_for  # noqa: E402


def test_initials_for_two_word_name_uses_first_and_last_initials() -> None:
    assert initials_for("Ada Lovelace") == "AL"


def test_initials_for_single_word_name_uses_first_two_letters() -> None:
    assert initials_for("Linus") == "LI"


def test_initials_for_email_only_input_falls_back_to_local_part() -> None:
    assert initials_for("", "torvalds@kernel.org") == "TO"


def test_initials_for_empty_inputs_returns_question_mark() -> None:
    assert initials_for("", "") == "?"


def test_color_for_is_deterministic_per_identity() -> None:
    first = color_for("Ada Lovelace", "ada@example.com")
    second = color_for("Ada Lovelace", "ada@example.com")
    assert first.name() == second.name()


def test_color_for_differs_between_distinct_identities() -> None:
    a = color_for("Ada Lovelace", "ada@example.com")
    b = color_for("Grace Hopper", "grace@example.com")
    # Not guaranteed by contract but with 12 palette entries and these inputs
    # they should land on different slots.
    assert a.name() != b.name()
