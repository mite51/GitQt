from __future__ import annotations

from gitqt.parsers.graph_layout import build_lane_layout
from gitqt.parsers.history import HistoryGraphRow


def _row(
    short: str,
    parents: tuple[str, ...] = (),
    *,
    refs: tuple[str, ...] = (),
    head_ref: str | None = None,
) -> HistoryGraphRow:
    return HistoryGraphRow(
        graph_prefix="*",
        commit_short_hash=short,
        author="Author",
        date="2026-04-23",
        summary=f"Summary {short}",
        refs=refs,
        head_ref=head_ref,
        parents=parents,
    )


def test_linear_history_produces_single_lane_with_vertical_segments() -> None:
    rows = [
        _row("A", parents=("B",)),
        _row("B", parents=("C",)),
        _row("C", parents=()),
    ]

    layouts = build_lane_layout(rows)

    assert [layout.node_lane for layout in layouts] == [0, 0, 0]
    assert [layout.total_lanes for layout in layouts] == [1, 1, 1]

    top = layouts[0]
    mid = layouts[1]
    bot = layouts[2]
    assert any(
        seg.kind == "out" and seg.from_lane == 0 and seg.to_lane == 0 for seg in top.segments
    )
    assert any(seg.kind == "in" and seg.from_lane == 0 and seg.to_lane == 0 for seg in mid.segments)
    assert all(seg.kind != "out" for seg in bot.segments)


def test_merge_commit_spawns_second_lane_for_second_parent() -> None:
    rows = [
        _row("M", parents=("A", "B")),
        _row("A", parents=("D",)),
        _row("B", parents=("D",)),
        _row("D", parents=()),
    ]

    layouts = build_lane_layout(rows)

    merge_layout = layouts[0]
    out_lanes = sorted(seg.to_lane for seg in merge_layout.segments if seg.kind == "out")
    assert out_lanes == [0, 1]

    # The follow-up rows for A and B stay on their respective lanes.
    assert layouts[1].node_lane == 0
    assert layouts[2].node_lane == 1

    # The shared ancestor D collapses both lanes back to lane 0.
    d_layout = layouts[3]
    incoming = [seg for seg in d_layout.segments if seg.kind == "in"]
    assert any(seg.from_lane == 1 and seg.to_lane == 0 for seg in incoming)
    # After D no lanes remain.
    assert d_layout.total_lanes >= 1


def test_fork_commit_after_merge_reuses_vacated_lane() -> None:
    rows = [
        _row("M", parents=("A", "B")),
        _row("A", parents=("D",)),
        _row("B", parents=("D",)),
        _row("D", parents=("E",)),
        _row("E", parents=()),
    ]

    layouts = build_lane_layout(rows)

    # After D collapses, the active layout should contract to a single lane.
    assert layouts[3].node_lane == 0
    assert layouts[4].node_lane == 0


def test_linear_history_out_and_in_segments_meet_at_shared_lane() -> None:
    """Regression guard that vertical lines between rows share a lane.

    The delegate extends "out" segments by 1px past the row bottom and starts
    "in" segments at the row top to visually close the per-row seam. At the
    layout level this asserts the semantic contract: consecutive rows in a
    linear history carry an out segment from the parent row into an in segment
    of the following row on the same lane/color.
    """

    rows = [
        _row("A", parents=("B",)),
        _row("B", parents=("C",)),
        _row("C", parents=()),
    ]

    layouts = build_lane_layout(rows)

    top_out = [seg for seg in layouts[0].segments if seg.kind == "out"]
    mid_in = [seg for seg in layouts[1].segments if seg.kind == "in"]
    mid_out = [seg for seg in layouts[1].segments if seg.kind == "out"]
    bot_in = [seg for seg in layouts[2].segments if seg.kind == "in"]

    assert top_out and mid_in
    assert top_out[0].to_lane == mid_in[0].from_lane == 0
    assert top_out[0].color_lane == mid_in[0].color_lane
    assert mid_out and bot_in
    assert mid_out[0].to_lane == bot_in[0].from_lane == 0
    assert mid_out[0].color_lane == bot_in[0].color_lane


def test_parents_outside_loaded_window_do_not_open_dangling_lanes() -> None:
    """Truncated history should not leak a vertical rail for unseen parents.

    When a commit at the bottom of the loaded window references a parent that
    is beyond ``--max-count`` (or otherwise excluded from the loaded set), the
    layout must close out that lane instead of leaving it open, which would
    otherwise paint a phantom rail through every following row.
    """

    rows = [
        _row("A", parents=("B",)),
        # B's parent "Z" is not present in the rows list.
        _row("B", parents=("Z",)),
        # A separate root commit further down: should sit on lane 0 because
        # the previous lane was closed out.
        _row("X", parents=()),
    ]

    layouts = build_lane_layout(rows)

    # The row whose parent is missing must not emit an "out" segment.
    b_layout = layouts[1]
    assert all(seg.kind != "out" for seg in b_layout.segments)

    # The next, unrelated commit can take lane 0 instead of 1.
    x_layout = layouts[2]
    assert x_layout.node_lane == 0
    assert x_layout.total_lanes == 1


def test_trunk_branch_is_promoted_to_lane_zero() -> None:
    """Side branches processed first should yield lane 0 to HEAD/master.

    Mirrors what GitKraken/Sourcetree do: when a side branch tip happens to
    be the most recent commit (and therefore is processed before master in
    ``git log --all`` order), the trunk would otherwise land on lane 1 with
    the side branch hogging lane 0. The post-processing pass swaps them so
    the trunk always sits on the leftmost lane.
    """

    rows = [
        # Side branch tip (no HEAD) opens lane 0 first.
        _row("S1", parents=("S2",), refs=("dev/feature",)),
        _row("S2", parents=("BASE",)),
        # Master branch tip (HEAD -> master) is processed second; without
        # promotion it would sit on lane 1.
        _row("M1", parents=("BASE",), refs=("master",), head_ref="master"),
        _row("BASE", parents=()),
    ]

    layouts = build_lane_layout(rows)

    # The HEAD commit's lane must be 0 after promotion.
    assert layouts[2].node_lane == 0
    # The side-branch tip should now be off the trunk lane.
    assert layouts[0].node_lane != 0
    # Side-branch chain should be consistently swapped.
    assert layouts[1].node_lane == layouts[0].node_lane


def test_main_branch_is_promoted_to_lane_zero_when_no_head_marker() -> None:
    rows = [
        _row("S1", parents=("BASE",), refs=("dev/feature",)),
        _row("M1", parents=("BASE",), refs=("origin/main",)),
        _row("BASE", parents=()),
    ]

    layouts = build_lane_layout(rows)

    # No HEAD marker, but ``origin/main`` falls back to trunk detection.
    assert layouts[1].node_lane == 0


def test_color_lanes_persist_across_vertical_continuations() -> None:
    rows = [
        _row("M", parents=("A", "B")),
        _row("A", parents=("C",)),
        _row("B", parents=("C",)),
        _row("C", parents=()),
    ]

    layouts = build_lane_layout(rows)

    # Lane 1 carries the second-parent color throughout its lifetime (from M
    # forking it for B, through A's row continuing it, until B's row absorbs
    # it back into lane 0 at the shared ancestor).
    lane_one_colors = {
        seg.color_lane
        for layout in layouts
        for seg in layout.segments
        if seg.from_lane == 1 or seg.to_lane == 1
    }
    # Only one color should be used for that lane across its lifetime.
    assert len(lane_one_colors) == 1
