from __future__ import annotations

import os
import sys

import pytest

from gitqt.parsers.history import HistoryGraphRow


@pytest.fixture(scope="module")
def _qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - missing Qt deps
        pytest.skip(f"PySide6 not available: {exc}")

    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication(sys.argv[:1])
        except Exception as exc:  # pragma: no cover - offscreen bootstrap failure
            pytest.skip(f"Unable to create QApplication: {exc}")
    yield app


def _row(
    *,
    hash_short: str = "abc1234",
    author: str = "Ada Lovelace",
    date: str = "2024-01-01",
    summary: str = "Add feature",
    refs: tuple[str, ...] = (),
    head_ref: str | None = None,
    graph_prefix: str = "*",
) -> HistoryGraphRow:
    return HistoryGraphRow(
        graph_prefix=graph_prefix,
        commit_short_hash=hash_short,
        author=author,
        date=date,
        summary=summary,
        refs=refs,
        head_ref=head_ref,
    )


def test_history_table_model_exposes_row_count_and_display_text(_qt_app) -> None:
    from PySide6.QtCore import Qt

    from gitqt.ui.history_view import HistoryTableModel

    model = HistoryTableModel()
    rows = [
        _row(hash_short="aaaaaaa", summary="Initial commit"),
        _row(hash_short="bbbbbbb", summary="Add tests", refs=("main",), head_ref="main"),
    ]
    model.set_rows(rows)

    assert model.rowCount() == 2
    assert model.columnCount() == 7

    message_index = model.index(1, HistoryTableModel.COLUMN_MESSAGE)
    display = model.data(message_index, Qt.ItemDataRole.DisplayRole)
    assert display == "Add tests"

    hash_index = model.index(0, HistoryTableModel.COLUMN_HASH)
    assert model.data(hash_index, Qt.ItemDataRole.DisplayRole) == "aaaaaaa"

    branch_index = model.index(1, HistoryTableModel.COLUMN_BRANCH)
    assert model.data(branch_index, Qt.ItemDataRole.DisplayRole) == "main"

    empty_branch_index = model.index(0, HistoryTableModel.COLUMN_BRANCH)
    assert model.data(empty_branch_index, Qt.ItemDataRole.DisplayRole) == ""


def test_history_table_model_header_labels_expose_gitkraken_columns(_qt_app) -> None:
    from PySide6.QtCore import Qt

    from gitqt.ui.history_view import HistoryTableModel

    model = HistoryTableModel()
    headers = [
        model.headerData(col, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        for col in range(model.columnCount())
    ]
    assert headers == [
        "Branch / Tag",
        "Graph",
        "Message",
        "Author",
        "Age",
        "Date",
        "Hash",
    ]


def test_history_table_model_branch_column_prefers_head_ref_over_first_ref(_qt_app) -> None:
    from PySide6.QtCore import Qt

    from gitqt.ui.history_view import HistoryTableModel

    model = HistoryTableModel()
    model.set_rows(
        [
            _row(
                hash_short="dddd001",
                refs=("origin/main", "main"),
                head_ref="main",
            ),
            _row(
                hash_short="dddd002",
                refs=("feature/x",),
                head_ref=None,
            ),
        ]
    )

    head_row_idx = model.index(0, HistoryTableModel.COLUMN_BRANCH)
    assert model.data(head_row_idx, Qt.ItemDataRole.DisplayRole) == "main"

    non_head_row_idx = model.index(1, HistoryTableModel.COLUMN_BRANCH)
    assert model.data(non_head_row_idx, Qt.ItemDataRole.DisplayRole) == "feature/x"


def test_history_table_model_exposes_structured_payload_for_delegate(_qt_app) -> None:
    from gitqt.ui.history_view import GRAPH_ROW_ROLE, HistoryGraphPayload, HistoryTableModel

    model = HistoryTableModel()
    model.set_rows(
        [
            _row(
                hash_short="cccccc1",
                graph_prefix="* |",
                refs=("main", "origin/main"),
                head_ref="main",
            ),
        ]
    )

    index = model.index(0, HistoryTableModel.COLUMN_GRAPH)
    payload = model.data(index, GRAPH_ROW_ROLE)

    assert isinstance(payload, HistoryGraphPayload)
    assert payload.commit_short_hash == "cccccc1"
    assert payload.head_ref == "main"
    assert "main" in payload.refs


def test_history_filter_proxy_model_filters_by_summary_author_and_refs(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryFilterProxyModel, HistoryTableModel

    model = HistoryTableModel()
    proxy = HistoryFilterProxyModel()
    proxy.setSourceModel(model)
    model.set_rows(
        [
            _row(hash_short="0000001", author="Ada", summary="Add README"),
            _row(hash_short="0000002", author="Linus", summary="Fix bug", refs=("release/v1",)),
            _row(hash_short="0000003", author="Ada", summary="Document API"),
        ]
    )

    proxy.set_search("ada")
    assert proxy.rowCount() == 2

    proxy.set_search("release")
    assert proxy.rowCount() == 1

    proxy.set_search("0000003")
    assert proxy.rowCount() == 1

    proxy.set_search("")
    assert proxy.rowCount() == 3


def test_history_table_model_append_rows_emits_incremental_insert(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryTableModel

    model = HistoryTableModel()
    model.set_rows([_row(hash_short="1111111")])

    inserted_calls: list[tuple[int, int]] = []

    def _on_inserted(_parent, first, last):
        inserted_calls.append((first, last))

    model.rowsInserted.connect(_on_inserted)
    model.append_rows([_row(hash_short="2222222"), _row(hash_short="3333333")])

    assert model.rowCount() == 3
    assert inserted_calls == [(1, 2)]


def test_history_view_column_visibility_toggle_hides_and_shows(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryTableModel, HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view.model.set_rows(
            [
                _row(hash_short="eeee001", refs=("main",), head_ref="main"),
            ]
        )

        view._set_column_visible(HistoryTableModel.COLUMN_AUTHOR, False)
        assert view._table.isColumnHidden(HistoryTableModel.COLUMN_AUTHOR)

        view._set_column_visible(HistoryTableModel.COLUMN_AUTHOR, True)
        assert not view._table.isColumnHidden(HistoryTableModel.COLUMN_AUTHOR)
    finally:
        view.deleteLater()


def test_history_view_collects_refs_for_branch_menu(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view.model.set_rows(
            [
                _row(hash_short="ffff001", refs=("main", "origin/main"), head_ref="main"),
                _row(hash_short="ffff002", refs=("feature/a",)),
                _row(hash_short="ffff003", refs=()),
            ]
        )

        refs = view._collect_known_refs()
        assert refs == ["feature/a", "main", "origin/main"]
    finally:
        view.deleteLater()


def test_history_view_multi_branch_selection_persists(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view.model.set_rows(
            [
                _row(hash_short="aaaa001", refs=("main",), head_ref="main"),
                _row(hash_short="aaaa002", refs=("feature/a",)),
                _row(hash_short="aaaa003", refs=("feature/b",)),
            ]
        )

        view._selected_refs.update({"main", "feature/a"})

        assert len(view._selected_refs) == 2
        assert "main" in view._selected_refs
        assert "feature/a" in view._selected_refs
    finally:
        view.deleteLater()


def test_history_view_load_repository_preserves_selected_refs_for_same_path(
    _qt_app,
) -> None:
    from gitqt.ui.history_view import HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view._repository_path = "/tmp/repo-a"
        view._selected_refs = {"main"}

        view.load_repository("/tmp/repo-a")
        assert view._selected_refs == {"main"}

        view.load_repository("/tmp/repo-b")
        assert view._selected_refs == set()
    finally:
        view.deleteLater()


def test_strip_remote_prefix_uses_known_remotes(_qt_app) -> None:
    from gitqt.ui.history_view import _strip_remote_prefix

    assert _strip_remote_prefix("origin/main", ("origin", "upstream")) == ("main", True)
    assert _strip_remote_prefix("upstream/feature/x", ("origin", "upstream")) == (
        "feature/x",
        True,
    )
    assert _strip_remote_prefix("main", ("origin", "upstream")) == ("main", False)
    # Fallback when no remotes are known still strips "origin/".
    assert _strip_remote_prefix("origin/main", ()) == ("main", True)


def test_history_payload_show_avatar_only_on_top_of_same_author_streak(
    _qt_app,
) -> None:
    """Avatars render only on the topmost commit of a same-author streak.

    Rows are ordered newest-first, so the streak's "top" is the lowest
    index in a run of consecutive same-author commits. The first row in
    a run gets ``show_avatar=True``; subsequent same-author rows fall
    back to a small lane-colored dot. A different author starts a new
    streak whose first row gets the avatar back.
    """

    from gitqt.ui.history_view import GRAPH_ROW_ROLE, HistoryTableModel

    model = HistoryTableModel()
    model.set_rows(
        [
            _row(hash_short="aaa0001", author="Ada", summary="top of ada streak"),
            _row(hash_short="aaa0002", author="Ada", summary="middle of ada streak"),
            _row(hash_short="aaa0003", author="Linus", summary="linus handoff"),
            _row(hash_short="aaa0004", author="Ada", summary="ada returns"),
        ]
    )

    expected = [True, False, True, True]
    for row_idx, want in enumerate(expected):
        payload = model.data(model.index(row_idx, HistoryTableModel.COLUMN_GRAPH), GRAPH_ROW_ROLE)
        assert (
            payload.show_avatar is want
        ), f"row {row_idx} expected show_avatar={want}, got {payload.show_avatar}"


def test_history_payload_show_avatar_uses_email_when_display_name_repeats(
    _qt_app,
) -> None:
    """Two distinct authors with same display name still both get avatars."""

    from gitqt.ui.history_view import GRAPH_ROW_ROLE, HistoryTableModel

    model = HistoryTableModel()
    rows = [
        _row(hash_short="bbb0001", author="Alex", summary="alex one"),
        _row(hash_short="bbb0002", author="Alex", summary="alex two"),
    ]
    rows[0].author_email = "alex@one.example"
    rows[1].author_email = "alex@two.example"
    model.set_rows(rows)

    payload_top = model.data(model.index(0, HistoryTableModel.COLUMN_GRAPH), GRAPH_ROW_ROLE)
    payload_below = model.data(model.index(1, HistoryTableModel.COLUMN_GRAPH), GRAPH_ROW_ROLE)
    assert payload_top.show_avatar is True
    assert payload_below.show_avatar is True


def test_history_payload_show_avatar_when_same_author_switches_lanes(
    _qt_app,
) -> None:
    """Same author on a different lane gets its own avatar.

    A run of merge commits by the same person can hop between lanes; we
    want every lane switch to re-show the avatar so the topology reads
    clearly. Here ``c1 -> c2`` stays on the same lane (avatar deduped),
    while ``c3`` is a side-branch tip on a new lane that merges back into
    the trunk at ``c2``, so ``c3`` should keep its avatar even though
    it's authored by the same person.
    """

    from gitqt.ui.history_view import GRAPH_ROW_ROLE, HistoryTableModel

    rows = [
        _row(hash_short="c1", author="Mira", summary="trunk tip"),
        _row(hash_short="c2", author="Mira", summary="merge of side"),
        _row(hash_short="c3", author="Mira", summary="side branch tip"),
        _row(hash_short="c4", author="Mira", summary="trunk parent"),
    ]
    rows[0].parents = ("c2",)
    rows[1].parents = ("c4", "c3")
    rows[2].parents = ("c4",)
    rows[3].parents = ()

    model = HistoryTableModel()
    model.set_rows(rows)

    # Sanity: c2 (merge) and c3 (side branch tip) should land on
    # different lanes so the lane-aware dedup actually has something to
    # disambiguate.
    layout_c2 = model.layout_at(1)
    layout_c3 = model.layout_at(2)
    assert layout_c2 is not None and layout_c3 is not None
    assert layout_c2.node_lane != layout_c3.node_lane

    expected = [True, False, True, True]
    for row_idx, want in enumerate(expected):
        payload = model.data(model.index(row_idx, HistoryTableModel.COLUMN_GRAPH), GRAPH_ROW_ROLE)
        assert (
            payload.show_avatar is want
        ), f"row {row_idx} expected show_avatar={want}, got {payload.show_avatar}"


def test_history_payload_inherited_ref_walks_upstream_on_same_lane(_qt_app) -> None:
    from gitqt.ui.history_view import GRAPH_ROW_ROLE, HistoryTableModel

    model = HistoryTableModel()
    model.set_rows(
        [
            _row(hash_short="bbb0001", refs=("main",), head_ref="main", summary="tip"),
            _row(hash_short="bbb0002", refs=(), summary="parent"),
            _row(hash_short="bbb0003", refs=(), summary="grandparent"),
        ]
    )

    # First row already has a primary_ref, no inherited ref needed.
    tip_payload = model.data(model.index(0, HistoryTableModel.COLUMN_GRAPH), GRAPH_ROW_ROLE)
    assert tip_payload.primary_ref == "main"
    assert tip_payload.inherited_ref is None

    # Second/third rows inherit "main" by walking up the lane.
    middle_payload = model.data(model.index(1, HistoryTableModel.COLUMN_GRAPH), GRAPH_ROW_ROLE)
    assert middle_payload.primary_ref is None
    assert middle_payload.inherited_ref == "main"

    last_payload = model.data(model.index(2, HistoryTableModel.COLUMN_GRAPH), GRAPH_ROW_ROLE)
    assert last_payload.inherited_ref == "main"


def test_history_view_select_none_clears_selected_refs(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view.model.set_rows(
            [
                _row(hash_short="cccc001", refs=("main",), head_ref="main"),
                _row(hash_short="cccc002", refs=("feature/a",)),
            ]
        )
        view._selected_refs = {"main", "feature/a"}
        view._rebuild_branch_menu()

        view._on_select_no_branches()

        assert view._selected_refs == set()
        for checkbox in view._branch_checkboxes.values():
            assert not checkbox.isChecked()
    finally:
        view.deleteLater()


def test_history_view_select_all_checks_visible_branches(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view.model.set_rows(
            [
                _row(hash_short="dddd001", refs=("main",), head_ref="main"),
                _row(hash_short="dddd002", refs=("feature/a",)),
                _row(hash_short="dddd003", refs=("feature/b",)),
            ]
        )
        view._rebuild_branch_menu()

        view._on_select_all_branches()

        assert view._selected_refs == {"main", "feature/a", "feature/b"}
        for checkbox in view._branch_checkboxes.values():
            assert checkbox.isChecked()
    finally:
        view.deleteLater()


def test_history_view_branch_menu_filter_hides_non_matching(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view.model.set_rows(
            [
                _row(hash_short="eeee001", refs=("main",), head_ref="main"),
                _row(hash_short="eeee002", refs=("feature/alpha",)),
                _row(hash_short="eeee003", refs=("feature/beta",)),
            ]
        )
        view._rebuild_branch_menu()

        view._on_branch_menu_filter_changed("feat")

        # Use isHidden() since the menu isn't shown on screen in tests.
        assert view._branch_checkboxes["main"].isHidden() is True
        assert view._branch_checkboxes["feature/alpha"].isHidden() is False
        assert view._branch_checkboxes["feature/beta"].isHidden() is False

        # Select-all only toggles visible entries -> "main" stays unchecked.
        view._on_select_all_branches()
        assert view._selected_refs == {"feature/alpha", "feature/beta"}
    finally:
        view.deleteLater()


def test_history_view_set_row_alpha_restyles_table(_qt_app) -> None:
    from gitqt.ui.history_view import HistoryView

    view = HistoryView()
    from gitqt.services.worker_pool import ImmediateWorkerPool

    view.set_worker_pool(ImmediateWorkerPool())
    try:
        view.set_row_alpha(0.1)
        sheet_a = view._table.styleSheet()

        view.set_row_alpha(0.9)
        sheet_b = view._table.styleSheet()

        assert sheet_a != sheet_b
        assert "rgba(30, 30, 46, 26)" in sheet_a  # round(0.1 * 255) == 26
        assert "rgba(30, 30, 46, 230)" in sheet_b  # round(0.9 * 255) == 230
    finally:
        view.deleteLater()


def test_history_view_age_column_exposes_relative_date(_qt_app) -> None:
    from PySide6.QtCore import Qt

    from gitqt.ui.history_view import HistoryTableModel

    model = HistoryTableModel()
    rows = [
        HistoryGraphRow(
            graph_prefix="*",
            commit_short_hash="feed001",
            author="Ada",
            date="2026-04-13",
            summary="Do thing",
            refs=(),
            head_ref=None,
            age="3 days ago",
        ),
    ]
    model.set_rows(rows)
    age_index = model.index(0, HistoryTableModel.COLUMN_AGE)
    assert model.data(age_index, Qt.ItemDataRole.DisplayRole) == "3 days ago"
