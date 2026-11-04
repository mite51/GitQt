from __future__ import annotations

import os
import sys

import pytest

from gitqt.parsers.refs import LocalBranchRef, RemoteBranchRef
from gitqt.services.branch import BranchSnapshot, BranchTrackingStatus


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
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Unable to create QApplication: {exc}")
    yield app


def _make_snapshot(
    local: list[LocalBranchRef],
    remote: list[RemoteBranchRef] | None = None,
) -> BranchSnapshot:
    return BranchSnapshot(
        local_branches=local,
        remote_branches=remote or [],
        tracking=BranchTrackingStatus(
            branch_name="main",
            upstream=None,
            ahead=0,
            behind=0,
            is_detached_head=False,
            head_commit_short_hash="abc1234",
        ),
    )


def _collect_tree(root) -> list[str]:
    """Flatten the visible item labels in depth-first order."""

    from PySide6.QtCore import Qt

    labels: list[str] = []

    def _walk(item) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            if child is None:
                continue
            labels.append(child.text(0))
            labels.append(str(child.data(0, Qt.ItemDataRole.UserRole) or ""))
            _walk(child)

    _walk(root)
    return labels


def test_sidebar_splits_local_branches_by_slash_when_enabled(_qt_app) -> None:
    from gitqt.ui.sidebar import Sidebar

    sidebar = Sidebar(split_branch_paths=True)
    try:
        snapshot = _make_snapshot(
            [
                LocalBranchRef(
                    name="feature/abc",
                    upstream=None,
                    is_head=False,
                    commit_short_hash="abc",
                ),
                LocalBranchRef(
                    name="feature/def",
                    upstream=None,
                    is_head=False,
                    commit_short_hash="def",
                ),
                LocalBranchRef(
                    name="main",
                    upstream=None,
                    is_head=True,
                    commit_short_hash="111",
                ),
            ]
        )
        sidebar.set_branch_snapshot(snapshot)

        local_root = sidebar._local_root
        assert local_root is not None
        labels = _collect_tree(local_root)
        # There should be a "feature" group, with "abc" and "def" leaves
        assert "feature" in labels
        assert "abc" in labels
        assert "def" in labels
        # Head indicator preserved on main
        assert "main" in labels
    finally:
        sidebar.deleteLater()


def test_sidebar_keeps_branches_flat_when_split_disabled(_qt_app) -> None:
    from gitqt.ui.sidebar import Sidebar

    sidebar = Sidebar(split_branch_paths=False)
    try:
        snapshot = _make_snapshot(
            [
                LocalBranchRef(
                    name="feature/abc",
                    upstream=None,
                    is_head=False,
                    commit_short_hash="abc",
                ),
            ]
        )
        sidebar.set_branch_snapshot(snapshot)

        local_root = sidebar._local_root
        assert local_root is not None
        labels = _collect_tree(local_root)
        assert "feature/abc" in labels
        assert "feature" not in labels
    finally:
        sidebar.deleteLater()


def test_sidebar_toggle_split_branch_paths_rebuilds_tree(_qt_app) -> None:
    from gitqt.ui.sidebar import Sidebar

    sidebar = Sidebar(split_branch_paths=False)
    try:
        snapshot = _make_snapshot(
            [
                LocalBranchRef(
                    name="feature/abc",
                    upstream=None,
                    is_head=False,
                    commit_short_hash="abc",
                ),
            ]
        )
        sidebar.set_branch_snapshot(snapshot)
        initial = _collect_tree(sidebar._local_root)
        assert "feature/abc" in initial
        assert "feature" not in initial

        sidebar.set_split_branch_paths(True)
        rebuilt = _collect_tree(sidebar._local_root)
        assert "feature" in rebuilt
        assert "abc" in rebuilt
    finally:
        sidebar.deleteLater()


def test_sidebar_selects_active_branch_and_shows_tracking_counts(_qt_app) -> None:
    from PySide6.QtCore import Qt

    from gitqt.ui.sidebar import Sidebar

    sidebar = Sidebar(split_branch_paths=True)
    try:
        snapshot = BranchSnapshot(
            local_branches=[
                LocalBranchRef(
                    name="feature/current",
                    upstream="origin/feature/current",
                    is_head=True,
                    commit_short_hash="abc",
                )
            ],
            remote_branches=[],
            tracking=BranchTrackingStatus(
                branch_name="feature/current",
                upstream="origin/feature/current",
                ahead=2,
                behind=1,
                is_detached_head=False,
                head_commit_short_hash="abc",
            ),
        )

        sidebar.set_branch_snapshot(snapshot)

        selected = sidebar._tree.currentItem()
        assert selected is not None
        assert selected.text(0) == "current  ↑2  ↓1"
        assert selected.data(0, Qt.ItemDataRole.UserRole) == (
            "local_branch",
            "feature/current",
        )
        from gitqt.ui.theme import active_branch_color

        active_color = active_branch_color(sidebar._tree.palette())
        assert selected.foreground(0).color() == active_color
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QStyleOptionViewItem

        option = QStyleOptionViewItem()
        option.widget = sidebar._tree
        sidebar._tree.itemDelegate().initStyleOption(
            option,
            sidebar._tree.indexFromItem(selected),
        )
        assert option.palette.color(QPalette.ColorRole.HighlightedText) == active_color
        assert selected.parent().isExpanded()
    finally:
        sidebar.deleteLater()


def test_branch_highlight_colors_adapt_to_dark_and_light_palettes(_qt_app) -> None:
    from PySide6.QtGui import QColor, QPalette

    from gitqt.ui.theme import active_branch_color, branch_selection_outline_color

    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#1e1e2e"))
    light = QPalette()
    light.setColor(QPalette.ColorRole.Window, QColor("#f5f5f5"))

    assert active_branch_color(dark) == QColor("#f9e2af")
    assert active_branch_color(light) == QColor("#8a5d00")
    assert branch_selection_outline_color(dark) == QColor("#fff4c2")
    assert branch_selection_outline_color(light) == QColor("#5c4300")


def test_sidebar_preserves_user_selection_during_same_branch_refresh(_qt_app) -> None:
    from gitqt.ui.sidebar import Sidebar

    sidebar = Sidebar(split_branch_paths=False)
    try:
        snapshot = BranchSnapshot(
            local_branches=[
                LocalBranchRef(
                    name="main",
                    upstream="origin/main",
                    is_head=True,
                    commit_short_hash="abc",
                ),
                LocalBranchRef(
                    name="topic",
                    upstream=None,
                    is_head=False,
                    commit_short_hash="def",
                ),
            ],
            remote_branches=[],
            tracking=BranchTrackingStatus(
                branch_name="main",
                upstream="origin/main",
                ahead=0,
                behind=0,
                is_detached_head=False,
                head_commit_short_hash="abc",
            ),
        )
        sidebar.set_branch_snapshot(snapshot)
        sidebar._tree.setCurrentItem(sidebar._local_root.child(1))

        sidebar.set_branch_snapshot(snapshot)

        assert sidebar._tree.currentItem().text(0) == "topic"
    finally:
        sidebar.deleteLater()
