"""Multi-lane graph layout from commit parent topology.

Takes a list of :class:`HistoryGraphRow` in topological (git log) order and
produces a :class:`RowLayout` per row describing where the commit node sits
and which vertical/diagonal edges should be drawn in that row.

The algorithm tracks "lanes" — at each moment, each lane slot is either
empty or holds the short hash of a commit the lane is currently waiting to
reach. When we encounter a commit:

1. All lanes currently expecting this commit collapse into its node lane
   (leftmost-wins by default). Other lanes continue vertically.
2. After emitting the node, the commit's parents populate lanes: the first
   parent stays in the commit's lane; remaining parents extend into new lanes
   (filling empty slots or appending on the right).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gitqt.parsers.history import HistoryGraphRow


@dataclass(slots=True, frozen=True)
class EdgeSegment:
    """A single line segment to draw inside a row."""

    from_lane: int
    to_lane: int
    color_lane: int
    kind: str  # "in" (top edge -> center) or "out" (center -> bottom edge)


@dataclass(slots=True)
class RowLayout:
    """Per-row drawing instructions for the graph delegate."""

    node_lane: int
    node_color_lane: int
    total_lanes: int
    segments: list[EdgeSegment] = field(default_factory=list)


def build_lane_layout(rows: list[HistoryGraphRow]) -> list[RowLayout]:
    """Compute per-row lane layouts from commit parent topology."""

    # Set of commit hashes that will actually be drawn. Parents pointing
    # outside this set (e.g. truncated by a ``git log --max-count`` limit)
    # do not get a lane allocated -- otherwise we leak a dangling vertical
    # rail that runs forever past the bottom of the loaded view with no
    # commit node and no branch tip to anchor it.
    known_hashes: set[str] = {row.commit_short_hash for row in rows}

    # active[i] is the short hash the lane is waiting for, or None if empty.
    active: list[str | None] = []
    # color lane assigned to the expected-hash in each active slot
    color: list[int] = []
    # rolling counter for brand-new lane colors so color does not repeat on
    # re-use of a previously vacated lane slot
    next_color = 0

    layouts: list[RowLayout] = []

    for row in rows:
        commit_hash = row.commit_short_hash

        incoming_lanes = [i for i, h in enumerate(active) if h == commit_hash]
        if incoming_lanes:
            node_lane = incoming_lanes[0]
            node_color_lane = color[node_lane]
        else:
            node_lane = _first_empty_slot(active)
            node_color_lane = next_color
            next_color += 1
            if node_lane == len(active):
                active.append(None)
                color.append(node_color_lane)
            else:
                color[node_lane] = node_color_lane

        total_lanes_before = len(active)

        segments: list[EdgeSegment] = []
        # Incoming edges: every occupied lane either continues (vertical) or
        # is absorbed into node_lane (diagonal).
        for lane in range(total_lanes_before):
            if active[lane] is None:
                continue
            if active[lane] == commit_hash:
                if lane == node_lane:
                    segments.append(
                        EdgeSegment(
                            from_lane=lane,
                            to_lane=lane,
                            color_lane=color[lane],
                            kind="in",
                        )
                    )
                else:
                    segments.append(
                        EdgeSegment(
                            from_lane=lane,
                            to_lane=node_lane,
                            color_lane=color[lane],
                            kind="in",
                        )
                    )
            else:
                segments.append(
                    EdgeSegment(
                        from_lane=lane,
                        to_lane=lane,
                        color_lane=color[lane],
                        kind="in",
                    )
                )

        # Clear absorbed lanes (other than the node lane) so their slots are
        # available to new parent branches.
        for lane in incoming_lanes:
            if lane != node_lane:
                active[lane] = None

        # Outgoing edges: place parents starting from the node lane. Parents
        # outside ``known_hashes`` are dropped so we do not leak rails past
        # the loaded portion of history.
        parents = [parent for parent in row.parents if parent in known_hashes]
        if not parents:
            active[node_lane] = None
        else:
            first_parent = parents[0]
            active[node_lane] = first_parent
            # keep node's color for the ongoing first-parent lane
            color[node_lane] = node_color_lane
            segments.append(
                EdgeSegment(
                    from_lane=node_lane,
                    to_lane=node_lane,
                    color_lane=node_color_lane,
                    kind="out",
                )
            )

            for parent in parents[1:]:
                target_lane = _first_empty_slot(active)
                new_color = next_color
                next_color += 1
                if target_lane == len(active):
                    active.append(parent)
                    color.append(new_color)
                else:
                    active[target_lane] = parent
                    color[target_lane] = new_color
                segments.append(
                    EdgeSegment(
                        from_lane=node_lane,
                        to_lane=target_lane,
                        color_lane=new_color,
                        kind="out",
                    )
                )

        # Trim trailing empty lanes so total_lanes stays meaningful.
        while active and active[-1] is None:
            active.pop()
            color.pop()

        total_lanes = max(total_lanes_before, len(active), node_lane + 1)
        layouts.append(
            RowLayout(
                node_lane=node_lane,
                node_color_lane=node_color_lane,
                total_lanes=total_lanes,
                segments=segments,
            )
        )

    return _promote_trunk_to_lane_zero(rows, layouts)


_TRUNK_NAMES = ("master", "main", "trunk", "develop")


def _promote_trunk_to_lane_zero(
    rows: list[HistoryGraphRow],
    layouts: list[RowLayout],
) -> list[RowLayout]:
    """Swap lanes so that the trunk (HEAD / master / main) sits on lane 0.

    Without this pass, whichever branch happened to be processed first by
    ``git log`` (often a recently-touched side branch) gets lane 0, pushing
    the conventional trunk to the right and producing the visually noisy
    "long red rail next to master" layout. Polished Git GUIs (GitKraken,
    GitX, Sourcetree) all keep the trunk on the leftmost lane; we mirror
    that convention by detecting the trunk lane and globally swapping.
    """

    trunk_lane: int | None = None

    # Highest-confidence trunk: row carrying ``HEAD -> <ref>``.
    for row, layout in zip(rows, layouts, strict=True):
        if row.head_ref is not None:
            trunk_lane = layout.node_lane
            break

    # Fallback: a row whose primary ref looks like a conventional trunk.
    if trunk_lane is None:
        for row, layout in zip(rows, layouts, strict=True):
            for ref in row.refs:
                tail = ref.rsplit("/", 1)[-1]
                if tail in _TRUNK_NAMES:
                    trunk_lane = layout.node_lane
                    break
            if trunk_lane is not None:
                break

    if trunk_lane is None or trunk_lane == 0:
        return layouts

    swap = {0: trunk_lane, trunk_lane: 0}
    swapped: list[RowLayout] = []
    for layout in layouts:
        new_segments = [
            EdgeSegment(
                from_lane=swap.get(seg.from_lane, seg.from_lane),
                to_lane=swap.get(seg.to_lane, seg.to_lane),
                color_lane=seg.color_lane,
                kind=seg.kind,
            )
            for seg in layout.segments
        ]
        new_node_lane = swap.get(layout.node_lane, layout.node_lane)
        # The swap can push the highest used lane index up (e.g. if only
        # lane 0 was occupied before and it became lane T after swap), so
        # ``total_lanes`` must grow to cover the new maximum lane index.
        used_lanes: list[int] = [new_node_lane]
        for seg in new_segments:
            used_lanes.append(seg.from_lane)
            used_lanes.append(seg.to_lane)
        new_total_lanes = max(layout.total_lanes, max(used_lanes) + 1)
        swapped.append(
            RowLayout(
                node_lane=new_node_lane,
                node_color_lane=layout.node_color_lane,
                total_lanes=new_total_lanes,
                segments=new_segments,
            )
        )
    return swapped


def _first_empty_slot(active: list[str | None]) -> int:
    for i, value in enumerate(active):
        if value is None:
            return i
    return len(active)
