"""History view: GitKraken-style commit graph with commit details panel.

Uses :class:`QTableView` with a :class:`HistoryTableModel` (backed by
:class:`HistoryGraphRow` data) and a :class:`GraphDelegate` that custom-paints
colored lane lines and avatar commit-node icons using the existing
``graph_prefix`` string as a lane hint. The top bar hosts a hamburger menu for
commit filter / multi-branch filter / column visibility, and the commit rows
expose a GitKraken-style context menu.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPoint,
    QRect,
    QSettings,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QHelpEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPixmapCache,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from gitqt.parsers.graph_layout import RowLayout, build_lane_layout
from gitqt.parsers.history import HistoryGraphRow
from gitqt.services.history import CommitFileChange, HistoryService
from gitqt.services.session_jobs import SessionJobs
from gitqt.services.worker_pool import WorkerPool
from gitqt.ui.avatars import best_text_color_for, render_avatar
from gitqt.ui.diff_view import DiffView
from gitqt.ui.theme import (
    ACCENT,
    BG_SURFACE_HOVER,
    BORDER,
    FG_DIM,
    FG_SECONDARY,
    GRAPH_COLORS,
    branch_selection_outline_color,
    mono_font,
    ui_font,
)

GRAPH_ROW_ROLE = Qt.ItemDataRole.UserRole + 1


# ---------------------------------------------------------------------------
# Ref helpers
# ---------------------------------------------------------------------------


def _strip_remote_prefix(ref: str, known_remotes: tuple[str, ...]) -> tuple[str, bool]:
    """Return ``(display_name, is_remote)`` stripping any remote prefix."""

    for remote in known_remotes:
        if not remote:
            continue
        prefix = f"{remote}/"
        if ref.startswith(prefix):
            return ref[len(prefix) :], True
    if ref.startswith("origin/"):
        return ref[len("origin/") :], True
    return ref, False


@dataclass(slots=True)
class HistoryGraphPayload:
    """Structured payload carried on ``GRAPH_ROW_ROLE`` for delegates.

    Carries the computed multi-lane layout so the delegate can draw lane
    edges and the commit node without recomputing topology. Also projects
    the row's primary ref for the Branch/Tag column pill delegate.
    """

    graph_prefix: str
    commit_short_hash: str
    head_ref: str | None
    refs: tuple[str, ...]
    primary_ref: str | None
    parents: tuple[str, ...] = ()
    extra_refs: tuple[str, ...] = field(default_factory=tuple)
    layout: RowLayout | None = None
    author: str = ""
    author_email: str = ""
    age: str = ""
    known_remotes: tuple[str, ...] = ()
    # True only on the commit that should carry the avatar node. For a streak
    # of commits by the same author, only one (the oldest in the streak) is
    # marked so.
    show_avatar: bool = True
    # Computed fallback ref for the Branch column when the row has no ref of
    # its own but we want to indicate the branch it belongs to (e.g. when the
    # row is selected but its branch tip is elsewhere).
    inherited_ref: str | None = None


class HistoryTableModel(QAbstractTableModel):
    """Model-backed history table that supports virtualized rendering."""

    COLUMN_BRANCH = 0
    COLUMN_GRAPH = 1
    COLUMN_MESSAGE = 2
    COLUMN_AUTHOR = 3
    COLUMN_AGE = 4
    COLUMN_DATE = 5
    COLUMN_HASH = 6

    _HEADERS = (
        "Branch / Tag",
        "Graph",
        "Message",
        "Author",
        "Age",
        "Date",
        "Hash",
    )

    @classmethod
    def header_label(cls, column: int) -> str:
        if 0 <= column < len(cls._HEADERS):
            return cls._HEADERS[column]
        return ""

    @classmethod
    def column_count(cls) -> int:
        return len(cls._HEADERS)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[HistoryGraphRow] = []
        self._layouts: list[RowLayout] = []
        self._max_lanes: int = 0
        self._known_remotes: tuple[str, ...] = ()

    # -----------------------------------------------------------------------
    # Data manipulation
    # -----------------------------------------------------------------------

    def rows(self) -> list[HistoryGraphRow]:
        return list(self._rows)

    def row_at(self, row_index: int) -> HistoryGraphRow | None:
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None

    def layout_at(self, row_index: int) -> RowLayout | None:
        if 0 <= row_index < len(self._layouts):
            return self._layouts[row_index]
        return None

    def max_lanes(self) -> int:
        return self._max_lanes

    def known_remotes(self) -> tuple[str, ...]:
        return self._known_remotes

    def set_known_remotes(self, remotes: Sequence[str]) -> None:
        self._known_remotes = tuple(sorted({r for r in remotes if r}))

    def set_rows(self, rows: list[HistoryGraphRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self._recompute_layouts()
        self.endResetModel()

    def append_rows(self, rows: list[HistoryGraphRow]) -> None:
        if not rows:
            return
        start = len(self._rows)
        end = start + len(rows) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._rows.extend(rows)
        self._recompute_layouts()
        self.endInsertRows()

    def clear(self) -> None:
        self.set_rows([])

    def _recompute_layouts(self) -> None:
        self._layouts = build_lane_layout(self._rows)
        self._max_lanes = max((layout.total_lanes for layout in self._layouts), default=0)

    # -----------------------------------------------------------------------
    # QAbstractTableModel API
    # -----------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if 0 <= section < len(self._HEADERS):
            return self._HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        column = index.column()

        if role == GRAPH_ROW_ROLE:
            primary = self._primary_ref_for(row)
            extras = tuple(ref for ref in row.refs if ref != primary)
            layout = self.layout_at(index.row())
            show_avatar = self._show_avatar_for(index.row())
            inherited_ref = None if primary else self._inherited_ref_for(index.row())
            return HistoryGraphPayload(
                graph_prefix=row.graph_prefix,
                commit_short_hash=row.commit_short_hash,
                head_ref=row.head_ref,
                refs=row.refs,
                primary_ref=primary,
                extra_refs=extras,
                parents=row.parents,
                layout=layout,
                author=row.author,
                author_email=row.author_email,
                age=row.age,
                known_remotes=self._known_remotes,
                show_avatar=show_avatar,
                inherited_ref=inherited_ref,
            )

        if role == Qt.ItemDataRole.DisplayRole:
            if column == self.COLUMN_BRANCH:
                return self._primary_ref_for(row) or ""
            if column == self.COLUMN_GRAPH:
                return row.graph_prefix
            if column == self.COLUMN_HASH:
                return row.commit_short_hash
            if column == self.COLUMN_MESSAGE:
                return row.summary
            if column == self.COLUMN_AUTHOR:
                return row.author
            if column == self.COLUMN_AGE:
                return row.age
            if column == self.COLUMN_DATE:
                return row.date
            return None

        if role == Qt.ItemDataRole.FontRole:
            if column == self.COLUMN_HASH:
                return mono_font(10)
            if column == self.COLUMN_MESSAGE and row.head_ref:
                return ui_font(10, bold=True)
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if column == self.COLUMN_HASH:
                return ACCENT
            if column == self.COLUMN_AUTHOR:
                return FG_SECONDARY
            if column in (self.COLUMN_DATE, self.COLUMN_AGE):
                return FG_DIM
            return None

        return None

    @staticmethod
    def _primary_ref_for(row: HistoryGraphRow) -> str | None:
        if row.head_ref:
            return row.head_ref
        if row.refs:
            return row.refs[0]
        return None

    def _show_avatar_for(self, row_index: int) -> bool:
        """Render the avatar only on the top entry of a same-author *and*
        same-lane streak.

        Rows are ordered newest-first, so the "top" of a streak is the
        smallest index that still belongs to it. We show the avatar when
        either the row above has a different author, or it sits on a
        different graph lane (i.e. a different branch trunk). The lane
        check matters because a single author can land commits on
        several branches in a row -- e.g. successive merges that move
        between feature branches -- and each lane switch should re-show
        the avatar to make it clear the commits belong to distinct
        topology rails.
        """

        row = self.row_at(row_index)
        if row is None:
            return False
        if row_index <= 0:
            return True
        prev = self.row_at(row_index - 1)
        if prev is None:
            return True
        if _author_key(prev) != _author_key(row):
            return True
        prev_layout = self.layout_at(row_index - 1)
        cur_layout = self.layout_at(row_index)
        if prev_layout is None or cur_layout is None:
            return True
        return prev_layout.node_lane != cur_layout.node_lane

    def _inherited_ref_for(self, row_index: int) -> str | None:
        """Return the closest ancestor ref reachable by walking toward newer
        commits along the same lane.

        Used to label a selected commit that has no ref of its own.
        """

        layout = self.layout_at(row_index)
        if layout is None:
            return None
        lane = layout.node_lane
        for i in range(row_index - 1, -1, -1):
            upstream_layout = self.layout_at(i)
            if upstream_layout is None:
                continue
            if upstream_layout.node_lane != lane:
                continue
            upstream_row = self.row_at(i)
            if upstream_row is None:
                continue
            primary = self._primary_ref_for(upstream_row)
            if primary:
                return primary
        return None


def _author_key(row: HistoryGraphRow) -> str:
    return (row.author_email or row.author or "").strip().lower()


class HistoryFilterProxyModel(QSortFilterProxyModel):
    """Proxy that filters commits by substring across summary/author/hash/refs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._search = ""
        self.setDynamicSortFilter(True)

    def set_search(self, text: str) -> None:
        normalized = text.lower().strip()
        if normalized == self._search:
            return
        self._search = normalized
        # Qt 6.10+ replaces invalidateRowsFilter; retain 6.8/6.9 support.
        if hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.invalidateFilter()

    def search_text(self) -> str:
        return self._search

    def filterAcceptsRow(self, source_row: int, _parent: QModelIndex) -> bool:
        if not self._search:
            return True
        source = self.sourceModel()
        if not isinstance(source, HistoryTableModel):
            return True
        row = source.row_at(source_row)
        if row is None:
            return False
        if self._search in row.summary.lower():
            return True
        if self._search in row.author.lower():
            return True
        if self._search in row.commit_short_hash.lower():
            return True
        if any(self._search in ref.lower() for ref in row.refs):
            return True
        return False


class GraphDelegate(QStyledItemDelegate):
    """Renders lane edges and a commit-node avatar using :class:`RowLayout`.

    For each row we draw the "in" segments (top edge -> row center) and the
    "out" segments (row center -> bottom edge) from the precomputed lane
    layout, then overlay the author avatar (wrapped with a lane-colored ring)
    on the row's node lane. When the row has a primary ref, we also draw a
    short horizontal "leader" from the left edge of the graph column to the
    node so the branch pill (in the column to our left) visually connects.
    """

    _LANE_STEP = 16  # horizontal pixels per lane
    _LANE_LEFT_MARGIN = 12
    _AVATAR_SIZE = 18
    _NODE_OUTLINE_WIDTH = 2.0
    _HEAD_OUTLINE_EXTRA = 1.5
    _LINE_WIDTH = 2
    # Radius of the rounded bend on lane-change segments. Kept small
    # (a few px) so the routing reads as a 90-degree GitKraken-style "L"
    # rather than a sweeping curve.
    _CORNER_RADIUS = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._highlighted_color_lane: int | None = None

    @property
    def highlighted_color_lane(self) -> int | None:
        return self._highlighted_color_lane

    def set_highlighted_color_lane(self, lane: int | None) -> None:
        self._highlighted_color_lane = lane

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.text = ""

        widget = style_option.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)

        payload = index.data(GRAPH_ROW_ROLE)
        if not isinstance(payload, HistoryGraphPayload):
            return
        layout = payload.layout
        if layout is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # The table view clips each delegate to its own cell rect, which was
        # causing a visible one-to-two pixel gap in the vertical rails
        # between consecutive rows. Disable clipping so each row's in/out
        # segments can extend into the neighbouring row and overlap there;
        # because rows paint top-to-bottom and every row repaints its own
        # background first, there is no stale bleed-through.
        painter.setClipping(False)

        rect = option.rect
        top = rect.top()
        bottom = rect.bottom()
        center_y = rect.center().y()
        # Extend segments a few pixels past the cell boundaries so in/out
        # lines genuinely overlap at the seam regardless of antialiasing.
        top_extend = top - 2
        bottom_extend = bottom + 2

        # The layout emits an ``in`` segment for every occupied lane in a
        # row but only emits ``out`` segments for the current commit's
        # parents. That means a lane that is merely passing through this
        # row (e.g. a side branch waiting for its tip lower down) has only
        # an ``in`` segment going ``top -> center`` and nothing for
        # ``center -> bottom``, leaving the bottom half of every
        # pass-through row blank and producing visibly dashed rails on the
        # right side of the graph. We detect those pass-through lanes
        # here and draw a single full-row vertical instead. Doing the
        # merge here also avoids a faint mid-row seam where the original
        # half-segments met under antialiasing.
        node_lane = layout.node_lane
        passthrough_lanes: set[int] = {
            seg.from_lane
            for seg in layout.segments
            if (seg.kind == "in" and seg.from_lane == seg.to_lane and seg.from_lane != node_lane)
        }
        drawn_passthrough: set[int] = set()
        outline_color = branch_selection_outline_color(
            widget.palette() if widget is not None else None
        )

        for segment in layout.segments:
            color = GRAPH_COLORS[segment.color_lane % len(GRAPH_COLORS)]
            segment_outline = (
                outline_color if segment.color_lane == self._highlighted_color_lane else None
            )
            if (
                segment.kind == "in"
                and segment.from_lane == segment.to_lane
                and segment.from_lane in passthrough_lanes
            ):
                if segment.from_lane in drawn_passthrough:
                    continue
                drawn_passthrough.add(segment.from_lane)
                x = self._lane_x(rect, segment.from_lane)
                self._draw_segment(
                    painter,
                    x,
                    top_extend,
                    x,
                    bottom_extend,
                    color,
                    outline_color=segment_outline,
                )
                continue
            if segment.kind == "in":
                self._draw_segment(
                    painter,
                    self._lane_x(rect, segment.from_lane),
                    top_extend,
                    self._lane_x(rect, segment.to_lane),
                    center_y,
                    color,
                    kind="in",
                    outline_color=segment_outline,
                )
            elif segment.kind == "out":
                self._draw_segment(
                    painter,
                    self._lane_x(rect, segment.from_lane),
                    center_y,
                    self._lane_x(rect, segment.to_lane),
                    bottom_extend,
                    color,
                    kind="out",
                    outline_color=segment_outline,
                )

        node_color = GRAPH_COLORS[layout.node_color_lane % len(GRAPH_COLORS)]
        node_x = self._lane_x(rect, layout.node_lane)

        # Only draw the leader to the avatar when the Branch/Tag pill will
        # actually be drawn alongside it. The pill delegate renders an
        # ``inherited_ref`` pill only for the currently selected row, so we
        # gate the leader on the same condition; otherwise we end up with
        # orphan horizontal lines across the graph for every commit that
        # could inherit a ref from upstream.
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        should_draw_leader = bool(payload.primary_ref) or (
            is_selected and bool(payload.inherited_ref)
        )
        if should_draw_leader:
            self._draw_leader(
                painter,
                rect.left(),
                node_x,
                center_y,
                node_color,
                outline_color=(
                    outline_color
                    if layout.node_color_lane == self._highlighted_color_lane
                    else None
                ),
            )

        if payload.show_avatar:
            self._draw_avatar_node(
                painter,
                node_x,
                center_y,
                node_color,
                payload.author,
                payload.author_email,
                payload.head_ref is not None,
            )
        else:
            self._draw_lane_dot(painter, node_x, center_y, node_color)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        payload = index.data(GRAPH_ROW_ROLE)
        lane_count = 1
        if isinstance(payload, HistoryGraphPayload) and payload.layout is not None:
            lane_count = max(1, payload.layout.total_lanes)
        width = self._LANE_LEFT_MARGIN * 2 + lane_count * self._LANE_STEP
        return QSize(width, 30)

    def _lane_x(self, rect: QRect, lane: int) -> int:
        return rect.left() + self._LANE_LEFT_MARGIN + lane * self._LANE_STEP

    def _draw_segment(
        self,
        painter: QPainter,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: QColor,
        *,
        kind: str = "in",
        outline_color: QColor | None = None,
    ) -> None:
        pen = QPen(color, self._LINE_WIDTH)
        if x1 == x2:
            # Flat cap on purely-vertical strokes so the endpoint pixels
            # are fully opaque -- round caps fade out under antialiasing
            # and were leaving faint gaps at the row-to-row seam.
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            if outline_color is not None:
                outline_pen = QPen(outline_color, self._LINE_WIDTH + 3)
                outline_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(outline_pen)
                painter.drawLine(x1, y1, x2, y2)
            painter.setPen(pen)
            painter.drawLine(x1, y1, x2, y2)
            return

        # GitKraken-style 90-degree "L" routing for lane-change segments.
        # An ``in`` segment comes down vertically in ``from_lane`` and
        # turns horizontally at ``center_y`` to land on the node's lane
        # at (x2, y2). An ``out`` segment leaves the node horizontally
        # at ``center_y`` and turns down vertically into ``to_lane``,
        # exiting through (x2, y2). A small quadratic curve smooths the
        # corner so the rails read as a polished "L" rather than a hard
        # pixelated bend.
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        sign_x = 1 if x2 > x1 else -1
        radius = min(
            self._CORNER_RADIUS,
            max(1, abs(x2 - x1) // 2),
            max(1, abs(y2 - y1) // 2),
        )

        path = QPainterPath()
        if kind == "in":
            corner_x = x1
            corner_y = y2
            path.moveTo(x1, y1)
            path.lineTo(corner_x, corner_y - radius)
            path.quadTo(
                corner_x,
                corner_y,
                corner_x + sign_x * radius,
                corner_y,
            )
            path.lineTo(x2, y2)
        else:  # "out"
            corner_x = x2
            corner_y = y1
            path.moveTo(x1, y1)
            path.lineTo(corner_x - sign_x * radius, corner_y)
            path.quadTo(
                corner_x,
                corner_y,
                corner_x,
                corner_y + radius,
            )
            path.lineTo(x2, y2)
        if outline_color is not None:
            outline_pen = QPen(outline_color, self._LINE_WIDTH + 3)
            outline_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(outline_pen)
            painter.drawPath(path)
        painter.setPen(pen)
        painter.drawPath(path)

    def _draw_leader(
        self,
        painter: QPainter,
        left: int,
        node_x: int,
        center_y: int,
        color: QColor,
        *,
        outline_color: QColor | None = None,
    ) -> None:
        end_x = node_x - self._AVATAR_SIZE // 2 - 1
        if outline_color is not None:
            outline_pen = QPen(outline_color, self._LINE_WIDTH + 3)
            outline_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(outline_pen)
            painter.drawLine(left, center_y, end_x, center_y)
        pen = QPen(color, self._LINE_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Draw from the very left edge of the graph column to just before the
        # avatar node. The pill is drawn flush to the right side of the branch
        # column, so this reads as a continuous leader.
        painter.drawLine(left, center_y, end_x, center_y)

    def _draw_lane_dot(
        self,
        painter: QPainter,
        cx: int,
        cy: int,
        color: QColor,
    ) -> None:
        """Small filled lane-colored dot used when the avatar is deduped.

        Keeps the lane visually anchored on the row without repeating the same
        author's avatar for a run of consecutive same-author commits.
        """

        radius = 4
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPoint(cx, cy), radius, radius)

    def _draw_avatar_node(
        self,
        painter: QPainter,
        cx: int,
        cy: int,
        color: QColor,
        author: str,
        email: str,
        is_head: bool,
    ) -> None:
        size = self._AVATAR_SIZE
        pixmap = render_avatar(author, email, size)
        painter.drawPixmap(cx - size // 2, cy - size // 2, pixmap)

        outline_width = self._NODE_OUTLINE_WIDTH + (self._HEAD_OUTLINE_EXTRA if is_head else 0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, outline_width))
        radius = size // 2 + 1
        painter.drawEllipse(QPoint(cx, cy), radius, radius)

        if is_head:
            highlight = QPen(color.lighter(160), 1)
            painter.setPen(highlight)
            painter.drawEllipse(QPoint(cx, cy), radius + 2, radius + 2)


class BranchPillDelegate(QStyledItemDelegate):
    """Renders the commit's primary ref as a colored rounded-pill label."""

    _PILL_PADDING_LEFT = 8
    _PILL_PADDING_RIGHT_TEXT = 8
    _PILL_PADDING_RIGHT_CLOUD = 4
    _PILL_PADDING_Y = 2
    _PILL_RADIUS = 8
    _CLOUD_W = 15
    _CLOUD_H = 10
    _CLOUD_GAP = 3
    # Right-edge inset between the pill and the column boundary. Kept
    # tight so the cloud glyph reads as part of the pill rather than
    # floating in column padding.
    _PILL_RIGHT_MARGIN = 0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Default to eliding from the left so the branch name's suffix
        # (the part the user typically scanned for) stays visible when
        # the pill is too narrow to fit the full ref.
        self._elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideLeft

    def set_elide_mode(self, mode: Qt.TextElideMode) -> None:
        """Update the elision direction used when the pill text overflows."""

        if self._elide_mode == mode:
            return
        self._elide_mode = mode
        parent = self.parent()
        if isinstance(parent, QWidget):
            viewport = parent.viewport() if hasattr(parent, "viewport") else None
            if viewport is not None:
                viewport.update()
            else:
                parent.update()

    def elide_mode(self) -> Qt.TextElideMode:
        return self._elide_mode

    def helpEvent(
        self,
        event: QHelpEvent,
        view: QAbstractItemView,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Show the full ref name in a tooltip on hover.

        The pill displays an elided form of the ref when the column is
        narrower than the text; the tooltip surfaces the un-elided value
        (and falls back to the inherited ref shown on a selected commit
        whose row has no ref of its own).
        """

        if event is not None and event.type() == QEvent.Type.ToolTip:
            full = self._tooltip_text_for(index)
            if full:
                QToolTip.showText(event.globalPos(), full, view)
                return True
            QToolTip.hideText()
            event.ignore()
            return True
        return super().helpEvent(event, view, option, index)

    @staticmethod
    def _tooltip_text_for(index: QModelIndex) -> str:
        payload = index.data(GRAPH_ROW_ROLE)
        if isinstance(payload, HistoryGraphPayload):
            if payload.primary_ref:
                if payload.extra_refs:
                    return ", ".join((payload.primary_ref, *payload.extra_refs))
                return payload.primary_ref
            if payload.inherited_ref:
                return payload.inherited_ref
        display = index.data(Qt.ItemDataRole.DisplayRole)
        return str(display) if isinstance(display, str) and display else ""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.text = ""

        widget = style_option.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)

        payload = index.data(GRAPH_ROW_ROLE)
        label_raw = index.data(Qt.ItemDataRole.DisplayRole)

        known_remotes: tuple[str, ...] = ()
        layout = None
        is_head = False
        is_inherited = False
        if isinstance(payload, HistoryGraphPayload):
            known_remotes = payload.known_remotes
            layout = payload.layout
            is_head = payload.head_ref is not None and payload.primary_ref == payload.head_ref
            is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
            if (not label_raw) and is_selected and payload.inherited_ref:
                label_raw = payload.inherited_ref
                is_inherited = True

        if not label_raw:
            return

        display_text, is_remote = _strip_remote_prefix(str(label_raw), known_remotes)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        fm = option.fontMetrics
        text_width = fm.horizontalAdvance(display_text)
        pill_height = fm.height() + self._PILL_PADDING_Y * 2
        right_padding = (
            self._PILL_PADDING_RIGHT_CLOUD if is_remote else self._PILL_PADDING_RIGHT_TEXT
        )
        cloud_space = (self._CLOUD_W + self._CLOUD_GAP) if is_remote else 0
        pill_width = text_width + self._PILL_PADDING_LEFT + right_padding + cloud_space

        available_w = option.rect.width() - (self._PILL_RIGHT_MARGIN + 2 + 2)
        if pill_width > available_w:
            max_text_w = max(
                0,
                available_w - self._PILL_PADDING_LEFT - right_padding - cloud_space,
            )
            display_text = fm.elidedText(display_text, self._elide_mode, max_text_w)
            text_width = fm.horizontalAdvance(display_text)
            pill_width = text_width + self._PILL_PADDING_LEFT + right_padding + cloud_space

        # Anchor pill flush with right edge of the Branch column so the graph
        # delegate's leader line reads as a continuous connection.
        pill_x = option.rect.right() - pill_width - self._PILL_RIGHT_MARGIN
        pill_x = max(pill_x, option.rect.left() + 2)
        pill_y = option.rect.center().y() - pill_height // 2
        pill_rect = QRect(pill_x, pill_y, pill_width, pill_height)

        lane_color = _lane_color_from_payload(payload)
        if lane_color is None:
            lane_color = QColor(ACCENT) if is_head else QColor(BG_SURFACE_HOVER)

        border_color = lane_color.darker(130) if lane_color else QColor(BORDER)
        text_color = best_text_color_for(lane_color)

        if is_inherited:
            # Soften inherited pills so they read as "this commit belongs to
            # the same branch" instead of a normal ref tip.
            fill = QColor(lane_color)
            fill.setAlpha(140)
            painter.setBrush(QBrush(fill))
            pen = QPen(border_color, 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
        else:
            painter.setBrush(QBrush(lane_color))
            painter.setPen(QPen(border_color, 1))
        painter.drawRoundedRect(pill_rect, self._PILL_RADIUS, self._PILL_RADIUS)

        text_rect = QRect(
            pill_rect.left() + self._PILL_PADDING_LEFT,
            pill_rect.top(),
            text_width,
            pill_rect.height(),
        )
        painter.setPen(text_color)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            display_text,
        )

        if is_remote:
            cloud_x = text_rect.right() + self._CLOUD_GAP
            cloud_y = pill_rect.center().y() - self._CLOUD_H // 2
            _draw_cloud_icon(
                painter,
                cloud_x,
                cloud_y,
                self._CLOUD_W,
                self._CLOUD_H,
                text_color,
            )

        # Hover tooltip with the un-elided ref is provided via
        # ``helpEvent`` -- nothing to do here.
        _ = layout  # silence unused when no payload variant
        painter.restore()


class AuthorDelegate(QStyledItemDelegate):
    """Renders the author column as a plain text label (avatar now in graph)."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.text = ""

        widget = style_option.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)

        payload = index.data(GRAPH_ROW_ROLE)
        author = ""
        if isinstance(payload, HistoryGraphPayload):
            author = payload.author
        display = index.data(Qt.ItemDataRole.DisplayRole)
        if not author and isinstance(display, str):
            author = display

        if not author:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        text_rect = QRect(
            option.rect.left() + 8,
            option.rect.top(),
            option.rect.width() - 12,
            option.rect.height(),
        )
        painter.setPen(FG_SECONDARY)
        fm = option.fontMetrics
        text = fm.elidedText(author, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )
        painter.restore()


# ---------------------------------------------------------------------------
# Helpers used by delegates
# ---------------------------------------------------------------------------


def _lane_color_from_payload(payload: Any) -> QColor | None:
    if not isinstance(payload, HistoryGraphPayload):
        return None
    layout = payload.layout
    if layout is None:
        return None
    return QColor(GRAPH_COLORS[layout.node_color_lane % len(GRAPH_COLORS)])


_CLOUD_SVG_RESOURCE = ("gitqt.ui.icons", "cloud_outline.svg")
_cloud_renderer: QSvgRenderer | None = None


def _cloud_svg_renderer() -> QSvgRenderer:
    """Return a process-wide :class:`QSvgRenderer` for the cloud icon.

    The renderer is parsed once on first use and reused thereafter; it
    is safe to share because we only call ``render()`` against
    short-lived per-row painters in the GUI thread.
    """

    global _cloud_renderer
    if _cloud_renderer is None:
        from importlib import resources

        package, name = _CLOUD_SVG_RESOURCE
        with resources.as_file(resources.files(package).joinpath(name)) as path:
            _cloud_renderer = QSvgRenderer(str(path))
    return _cloud_renderer


def _draw_cloud_icon(
    painter: QPainter,
    x: int,
    y: int,
    width: int,
    height: int,
    color: QColor,
) -> None:
    """Draw a tinted cloud glyph indicating an on-server ref.

    The cloud silhouette comes from a bundled SVG asset and is
    re-tinted to ``color`` via a ``CompositionMode_SourceIn`` pass so
    the same artwork blends with whatever pill background the pill
    delegate picked. Tinted pixmaps are cached in :class:`QPixmapCache`
    keyed by size + color so re-renders are essentially free.
    """

    cache_key = (
        f"gitqt_cloud::{width}x{height}::"
        f"{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}"
    )
    pixmap = QPixmap()
    if not QPixmapCache.find(cache_key, pixmap):
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        local_painter = QPainter(pixmap)
        try:
            local_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            local_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            _cloud_svg_renderer().render(local_painter, QRect(0, 0, width, height))
            local_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            local_painter.fillRect(0, 0, width, height, color)
        finally:
            local_painter.end()
        QPixmapCache.insert(cache_key, pixmap)

    painter.drawPixmap(x, y, pixmap)


class HistoryView(QWidget):
    """Commit history graph and details panel."""

    commit_selected = Signal(str)
    compare_commit_requested = Signal(str)
    checkout_commit_requested = Signal(str)
    create_branch_from_commit_requested = Signal(str)
    create_tag_at_commit_requested = Signal(str)
    cherry_pick_commit_requested = Signal(str)
    revert_commit_requested = Signal(str)
    merge_commit_requested = Signal(str)
    rebase_onto_commit_requested = Signal(str)
    interactive_rebase_requested = Signal(str)
    reset_to_commit_requested = Signal(str, str)  # (commit_hash, mode)

    _SEARCH_DEBOUNCE_MS = 200
    _INITIAL_LIMIT = 200
    _PAGE_SIZE = 200
    _ROW_HEIGHT = 30

    _SETTINGS_ORG = "GitQt"
    _SETTINGS_APP = "GitQt"
    _SETTINGS_WIDTHS_KEY = "history/column_widths"
    _SETTINGS_HIDDEN_KEY = "history/hidden_columns"
    _SETTINGS_PILL_ELIDE_LEFT_KEY = "history/branch_pill_elide_left"

    _DEFAULT_COLUMN_WIDTHS = {
        HistoryTableModel.COLUMN_BRANCH: 160,
        HistoryTableModel.COLUMN_GRAPH: 180,
        HistoryTableModel.COLUMN_MESSAGE: 420,
        HistoryTableModel.COLUMN_AUTHOR: 160,
        HistoryTableModel.COLUMN_AGE: 90,
        HistoryTableModel.COLUMN_DATE: 100,
        HistoryTableModel.COLUMN_HASH: 90,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history_service: HistoryService | None = None
        self._worker_pool: WorkerPool | None = None
        self._jobs = SessionJobs(parent=self)
        self._repository_path: str | None = None
        self._current_limit = self._INITIAL_LIMIT
        self._loading = False
        self._has_more = False
        self._selected_refs: set[str] = set()
        # Full set of refs observed across loads for this repo. Kept
        # independent of the currently-displayed rows so that filtering by a
        # branch does not shrink the branch checklist in the hamburger menu.
        self._all_known_refs: set[str] = set()
        self._known_remotes: tuple[str, ...] = ()

        self._current_commit_hash: str | None = None
        self._current_files: list[CommitFileChange] = []

        self._pending_search: str | None = None
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(self._SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._commit_search_change)

        self._model = HistoryTableModel(self)
        self._proxy = HistoryFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        self._column_actions: dict[int, Any] = {}
        self._branch_menu: QMenu | None = None
        self._branch_checkboxes: dict[str, QCheckBox] = {}
        self._branch_menu_filter: QLineEdit | None = None
        self._branch_menu_filter_text: str = ""
        self._settings = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        self._branch_pill_delegate: BranchPillDelegate | None = None
        self._graph_delegate: GraphDelegate | None = None
        self._pill_elide_left: bool = self._read_pill_elide_left_setting()
        self._pill_elide_left_action: Any = None
        self._pending_details_splitter_sizes: list[int] | None = None
        self._details_splitter_restore = QTimer(self)
        self._details_splitter_restore.setSingleShot(True)
        self._details_splitter_restore.timeout.connect(self._restore_details_splitter_sizes)

        self._build_ui()
        self._restore_column_state()
        self._apply_pill_elide_mode()
        self._update_filter_summary()
        self._show_details(None)

    def _read_pill_elide_left_setting(self) -> bool:
        raw = self._settings.value(self._SETTINGS_PILL_ELIDE_LEFT_KEY, True)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() not in ("false", "0", "no", "")
        try:
            return bool(int(raw))
        except (TypeError, ValueError):
            return True

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 6, 8, 6)

        self._menu_button = QToolButton()
        self._menu_button.setText("\u2630")
        self._menu_button.setToolTip("History filters and column options")
        self._menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_button.setAutoRaise(True)
        self._menu_button.setStyleSheet(
            "QToolButton { color: #cdd6f4; font-size: 16pt; padding: 0 8px; }"
            "QToolButton::menu-indicator { image: none; width: 0; }"
            "QToolButton:hover { color: #89b4fa; }"
        )
        top_bar.addWidget(self._menu_button)

        self._filter_summary = QLabel()
        self._filter_summary.setProperty("dim", True)
        self._filter_summary.setFont(ui_font(10))
        top_bar.addWidget(self._filter_summary)

        top_bar.addStretch()
        layout.addLayout(top_bar)

        self._menu_button.setMenu(self._build_filter_menu())

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setMouseTracking(True)
        self._graph_delegate = GraphDelegate(self._table)
        self._table.setItemDelegateForColumn(HistoryTableModel.COLUMN_GRAPH, self._graph_delegate)
        self._branch_pill_delegate = BranchPillDelegate(self._table)
        self._table.setItemDelegateForColumn(
            HistoryTableModel.COLUMN_BRANCH, self._branch_pill_delegate
        )
        self._table.setItemDelegateForColumn(
            HistoryTableModel.COLUMN_AUTHOR, AuthorDelegate(self._table)
        )
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        for col in range(HistoryTableModel.column_count()):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(HistoryTableModel.COLUMN_MESSAGE, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionsMovable(True)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._on_header_context_menu)
        hdr.sectionResized.connect(self._on_section_resized)

        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(self._ROW_HEIGHT)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_row_context_menu)
        self._row_alpha: float = 0.5
        self._apply_row_alpha_stylesheet()
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.setAutoFillBackground(False)
        selection_model = self._table.selectionModel()
        if selection_model is not None:
            selection_model.currentRowChanged.connect(self._on_current_row_changed)
        scrollbar = self._table.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.valueChanged.connect(self._on_scroll_changed)

        self._splitter.addWidget(self._table)

        self._details_panel = self._build_details_panel()
        self._details_panel.setMinimumWidth(0)
        self._details_panel.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        self._splitter.addWidget(self._details_panel)
        self._splitter.setCollapsible(1, True)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([500, 500])

        layout.addWidget(self._splitter, stretch=1)

    def _build_details_panel(self) -> QWidget:
        panel = QWidget()
        details_layout = QVBoxLayout(panel)
        details_layout.setContentsMargins(12, 10, 12, 10)
        details_layout.setSpacing(8)

        self._details_hash = QLabel()
        self._details_hash.setFont(mono_font(11))
        self._details_hash.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self._details_hash)

        self._details_branch = QLabel()
        self._details_branch.setProperty("subheading", True)
        self._details_branch.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self._details_branch)

        self._details_author = QLabel()
        self._details_author.setProperty("subheading", True)
        details_layout.addWidget(self._details_author)

        self._details_date = QLabel()
        self._details_date.setProperty("dim", True)
        details_layout.addWidget(self._details_date)

        self._details_refs = QLabel()
        self._details_refs.setWordWrap(True)
        details_layout.addWidget(self._details_refs)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("background: #45475a; border: none;")
        sep1.setFixedHeight(1)
        details_layout.addWidget(sep1)

        msg_title = QLabel("Message")
        msg_title.setFont(ui_font(10, bold=True))
        msg_title.setProperty("dim", True)
        details_layout.addWidget(msg_title)

        self._details_message = QLabel()
        self._details_message.setWordWrap(True)
        self._details_message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self._details_message)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background: #45475a; border: none;")
        sep2.setFixedHeight(1)
        details_layout.addWidget(sep2)

        files_title = QLabel("Affected files")
        files_title.setFont(ui_font(10, bold=True))
        files_title.setProperty("dim", True)
        details_layout.addWidget(files_title)

        self._details_files = QListWidget()
        self._details_files.setFont(mono_font(10))
        self._details_files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._details_files.setStyleSheet(
            "QListWidget { background: #181825; border: 1px solid #45475a; border-radius: 4px; }"
        )
        self._details_files.currentItemChanged.connect(self._on_file_selected)
        details_layout.addWidget(self._details_files, stretch=1)

        self._details_diff = DiffView()
        details_layout.addWidget(self._details_diff, stretch=2)

        self._details_empty = QLabel("Select a commit to see details.")
        self._details_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._details_empty.setProperty("dim", True)
        details_layout.addWidget(self._details_empty)

        panel.setStyleSheet("QWidget { border-left: 1px solid #45475a; }")
        return panel

    # -----------------------------------------------------------------------
    # Hamburger menu
    # -----------------------------------------------------------------------

    def _build_filter_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setMinimumWidth(300)

        filter_container = QWidget(menu)
        filter_layout = QVBoxLayout(filter_container)
        filter_layout.setContentsMargins(10, 8, 10, 6)
        filter_layout.setSpacing(4)

        filter_label = QLabel("Filter commits")
        filter_label.setFont(ui_font(9, bold=True))
        filter_label.setProperty("dim", True)
        filter_layout.addWidget(filter_label)

        self._filter_input = QLineEdit(filter_container)
        self._filter_input.setPlaceholderText("Filter by message, author, hash, or ref…")
        self._filter_input.textChanged.connect(self._on_filter_input_changed)
        filter_layout.addWidget(self._filter_input)

        filter_action = QWidgetAction(menu)
        filter_action.setDefaultWidget(filter_container)
        menu.addAction(filter_action)

        menu.addSeparator()

        self._branch_menu = menu.addMenu("Branches")
        self._branch_menu.aboutToShow.connect(self._rebuild_branch_menu)
        self._rebuild_branch_menu()

        columns_menu = menu.addMenu("Columns")
        self._column_actions = {}
        for col in range(HistoryTableModel.column_count()):
            action = columns_menu.addAction(HistoryTableModel.header_label(col))
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(
                lambda checked, column=col: self._set_column_visible(column, checked)
            )
            self._column_actions[col] = action

        menu.addSeparator()
        self._pill_elide_left_action = menu.addAction("Elide branch names from left")
        self._pill_elide_left_action.setCheckable(True)
        self._pill_elide_left_action.setChecked(self._pill_elide_left)
        self._pill_elide_left_action.setToolTip(
            "When checked, long branch names truncate with a leading "
            "ellipsis so the suffix stays visible. Otherwise they "
            "truncate from the right."
        )
        self._pill_elide_left_action.toggled.connect(self._on_pill_elide_left_toggled)

        menu.addSeparator()
        reset_action = menu.addAction("Reset column widths")
        reset_action.triggered.connect(self._reset_column_widths)

        refresh_action = menu.addAction("Refresh")
        refresh_action.triggered.connect(self.refresh)

        return menu

    def _rebuild_branch_menu(self) -> None:
        if self._branch_menu is None:
            return
        self._branch_menu.clear()
        self._branch_checkboxes.clear()

        # Top controls: Select all / Select none + filter text box. Kept in a
        # single QWidgetAction so the menu stays open on interaction.
        controls_host = QWidget(self._branch_menu)
        controls_layout = QVBoxLayout(controls_host)
        controls_layout.setContentsMargins(10, 6, 10, 4)
        controls_layout.setSpacing(4)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(6)

        select_all_btn = QPushButton("Select all")
        select_all_btn.setAutoDefault(False)
        select_all_btn.setDefault(False)
        select_all_btn.clicked.connect(self._on_select_all_branches)
        buttons_row.addWidget(select_all_btn)

        select_none_btn = QPushButton("Select none")
        select_none_btn.setAutoDefault(False)
        select_none_btn.setDefault(False)
        select_none_btn.clicked.connect(self._on_select_no_branches)
        buttons_row.addWidget(select_none_btn)
        buttons_row.addStretch()
        controls_layout.addLayout(buttons_row)

        self._branch_menu_filter = QLineEdit(controls_host)
        self._branch_menu_filter.setPlaceholderText("Filter branches\u2026")
        self._branch_menu_filter.setText(self._branch_menu_filter_text)
        self._branch_menu_filter.textChanged.connect(self._on_branch_menu_filter_changed)
        controls_layout.addWidget(self._branch_menu_filter)

        controls_action = QWidgetAction(self._branch_menu)
        controls_action.setDefaultWidget(controls_host)
        self._branch_menu.addAction(controls_action)

        refs = self._collect_known_refs()
        if not refs:
            return

        self._branch_menu.addSeparator()

        scroll_host = QWidget(self._branch_menu)
        scroll_layout = QVBoxLayout(scroll_host)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(0)

        inner = QWidget(scroll_host)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(10, 4, 10, 4)
        inner_layout.setSpacing(2)

        for ref in refs:
            checkbox = QCheckBox(ref)
            checkbox.setChecked(ref in self._selected_refs)
            checkbox.toggled.connect(
                lambda checked, ref_name=ref: self._on_branch_checkbox_toggled(ref_name, checked)
            )
            inner_layout.addWidget(checkbox)
            self._branch_checkboxes[ref] = checkbox

        # Trailing stretch prevents the visible checkboxes from being spread
        # out vertically when most entries are hidden by the filter (the
        # scroll area's ``setWidgetResizable`` stretches ``inner`` to fill
        # the viewport, so without this the extra space gets distributed
        # between rows).
        inner_layout.addStretch(1)

        scroll = QScrollArea(scroll_host)
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Pin the scroll area to a height based on the full branch list so
        # that filtering (which toggles checkbox visibility) does not shrink
        # the menu. Otherwise, filtering to a few branches and then clearing
        # the filter would leave the menu too short to show the rest without
        # closing and reopening it.
        sample_cb = next(iter(self._branch_checkboxes.values()), None)
        row_h = sample_cb.sizeHint().height() if sample_cb is not None else 20
        margins = inner_layout.contentsMargins()
        spacing = inner_layout.spacing()
        content_h = (
            margins.top() + margins.bottom() + len(refs) * row_h + max(0, len(refs) - 1) * spacing
        )
        scroll.setFixedHeight(max(60, min(260, content_h)))
        scroll_layout.addWidget(scroll)

        scroll_action = QWidgetAction(self._branch_menu)
        scroll_action.setDefaultWidget(scroll_host)
        self._branch_menu.addAction(scroll_action)

        # Apply the current filter text so visibility is consistent on open.
        self._apply_branch_menu_filter()
        # Keep focus on the filter so the user can start typing immediately.
        self._branch_menu_filter.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_select_all_branches(self) -> None:
        """Check every branch currently visible (not filtered out) in the list."""

        changed = False
        for ref, checkbox in self._branch_checkboxes.items():
            # ``isHidden`` is authoritative even before the menu is shown;
            # ``isVisible`` returns False until the parent is realized.
            if checkbox.isHidden():
                continue
            if ref in self._selected_refs:
                continue
            self._selected_refs.add(ref)
            checkbox.blockSignals(True)
            checkbox.setChecked(True)
            checkbox.blockSignals(False)
            changed = True
        if changed:
            self._refresh_for_filter_change()

    def _on_select_no_branches(self) -> None:
        """Clear every branch selection (reverts to showing all refs)."""

        if not self._selected_refs:
            return
        self._selected_refs.clear()
        for checkbox in self._branch_checkboxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        self._refresh_for_filter_change()

    def _on_branch_menu_filter_changed(self, text: str) -> None:
        self._branch_menu_filter_text = text
        self._apply_branch_menu_filter()

    def _apply_branch_menu_filter(self) -> None:
        needle = self._branch_menu_filter_text.strip().lower()
        for ref, checkbox in self._branch_checkboxes.items():
            checkbox.setVisible(not needle or needle in ref.lower())

    def _on_branch_checkbox_toggled(self, ref: str, checked: bool) -> None:
        if checked:
            self._selected_refs.add(ref)
        else:
            self._selected_refs.discard(ref)
        self._refresh_for_filter_change()

    def _refresh_for_filter_change(self) -> None:
        self._current_limit = self._INITIAL_LIMIT
        self._has_more = False
        self._refresh_graph()
        self._update_filter_summary()

    def _collect_known_refs(self) -> list[str]:
        # Union of refs observed across loads plus any refs currently in the
        # model. This keeps the branch checklist stable while a branch filter
        # is active (filtered loads return a subset of refs).
        seen: set[str] = set(self._all_known_refs)
        for row in self._model.rows():
            for ref in row.refs:
                if ref:
                    seen.add(ref)
        # Always include any currently selected refs so the user can always
        # uncheck them even if they are not present in the latest result.
        seen.update(self._selected_refs)
        return sorted(seen)

    def _set_column_visible(self, column: int, visible: bool) -> None:
        self._table.setColumnHidden(column, not visible)
        self._persist_hidden_columns()
        self._update_filter_summary()

    def _reset_column_widths(self) -> None:
        hdr = self._table.horizontalHeader()
        for col, width in self._DEFAULT_COLUMN_WIDTHS.items():
            hdr.resizeSection(col, width)
        self._persist_column_widths()

    def _on_pill_elide_left_toggled(self, checked: bool) -> None:
        self._pill_elide_left = bool(checked)
        self._settings.setValue(self._SETTINGS_PILL_ELIDE_LEFT_KEY, self._pill_elide_left)
        self._apply_pill_elide_mode()

    def _apply_pill_elide_mode(self) -> None:
        delegate = self._branch_pill_delegate
        if delegate is None:
            return
        mode = Qt.TextElideMode.ElideLeft if self._pill_elide_left else Qt.TextElideMode.ElideRight
        delegate.set_elide_mode(mode)

    # -----------------------------------------------------------------------
    # Column state persistence
    # -----------------------------------------------------------------------

    def _restore_column_state(self) -> None:
        hdr = self._table.horizontalHeader()
        raw_widths = self._settings.value(self._SETTINGS_WIDTHS_KEY)
        widths = self._parse_column_map(raw_widths)
        for col in range(HistoryTableModel.column_count()):
            width = widths.get(col) or self._DEFAULT_COLUMN_WIDTHS.get(col, 120)
            hdr.resizeSection(col, int(width))

        raw_hidden = self._settings.value(self._SETTINGS_HIDDEN_KEY)
        hidden_columns = self._parse_hidden_list(raw_hidden)
        for col in range(HistoryTableModel.column_count()):
            is_hidden = col in hidden_columns
            self._table.setColumnHidden(col, is_hidden)
            action = self._column_actions.get(col)
            if action is not None:
                action.setChecked(not is_hidden)

    def _on_section_resized(self, *_args: Any) -> None:
        self._persist_column_widths()

    def _persist_column_widths(self) -> None:
        hdr = self._table.horizontalHeader()
        widths = {str(col): hdr.sectionSize(col) for col in range(HistoryTableModel.column_count())}
        self._settings.setValue(self._SETTINGS_WIDTHS_KEY, widths)

    def _persist_hidden_columns(self) -> None:
        hidden = [
            col
            for col in range(HistoryTableModel.column_count())
            if self._table.isColumnHidden(col)
        ]
        self._settings.setValue(self._SETTINGS_HIDDEN_KEY, hidden)

    @staticmethod
    def _parse_column_map(raw: Any) -> dict[int, int]:
        if not isinstance(raw, dict):
            return {}
        parsed: dict[int, int] = {}
        for key, value in raw.items():
            try:
                parsed[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return parsed

    @staticmethod
    def _parse_hidden_list(raw: Any) -> set[int]:
        if raw is None:
            return set()
        if isinstance(raw, (list, tuple)):
            iterable = raw
        elif isinstance(raw, str):
            iterable = [piece for piece in raw.split(",") if piece.strip()]
        else:
            return set()
        hidden: set[int] = set()
        for item in iterable:
            try:
                hidden.add(int(item))
            except (TypeError, ValueError):
                continue
        return hidden

    # -----------------------------------------------------------------------
    # Filter summary
    # -----------------------------------------------------------------------

    def _update_filter_summary(self) -> None:
        parts: list[str] = []
        search = self._proxy.search_text()
        if search:
            parts.append(f'filter: "{search}"')
        if self._selected_refs:
            if len(self._selected_refs) == 1:
                only = next(iter(self._selected_refs))
                parts.append(f"branch: {only}")
            else:
                parts.append(f"branches: {len(self._selected_refs)}")
        hidden = [
            HistoryTableModel.header_label(col)
            for col in range(HistoryTableModel.column_count())
            if self._table.isColumnHidden(col)
        ]
        if hidden:
            parts.append(f"hidden: {len(hidden)}")
        self._filter_summary.setText(" \u2022 ".join(parts))

    # -----------------------------------------------------------------------
    # Row context menu
    # -----------------------------------------------------------------------

    def _on_row_context_menu(self, position: QPoint) -> None:
        index = self._table.indexAt(position)
        if not index.isValid():
            return
        source_index = self._proxy.mapToSource(index)
        row = self._model.row_at(source_index.row())
        if row is None:
            return

        menu = QMenu(self._table)

        def emit_commit(signal):
            return lambda _checked=False: signal.emit(row.commit_short_hash)

        compare_action = menu.addAction("Inspect commit and compare…")
        compare_action.triggered.connect(
            lambda _checked=False: self.compare_commit_requested.emit(
                row.commit_full_hash or row.commit_short_hash
            )
        )

        checkout_action = menu.addAction("Checkout commit")
        checkout_action.triggered.connect(emit_commit(self.checkout_commit_requested))

        branch_action = menu.addAction("Create branch here…")
        branch_action.triggered.connect(emit_commit(self.create_branch_from_commit_requested))

        tag_action = menu.addAction("Create tag here…")
        tag_action.triggered.connect(emit_commit(self.create_tag_at_commit_requested))

        menu.addSeparator()

        cherry_pick_action = menu.addAction("Cherry-pick commit…")
        cherry_pick_action.triggered.connect(emit_commit(self.cherry_pick_commit_requested))

        revert_action = menu.addAction("Revert commit…")
        revert_action.triggered.connect(emit_commit(self.revert_commit_requested))

        merge_action = menu.addAction("Merge commit into current branch…")
        merge_action.triggered.connect(emit_commit(self.merge_commit_requested))

        rebase_action = menu.addAction("Rebase current branch onto commit…")
        rebase_action.triggered.connect(emit_commit(self.rebase_onto_commit_requested))
        interactive_action = menu.addAction("Interactive rebase current branch onto commit…")
        interactive_action.triggered.connect(
            lambda _checked=False: self.interactive_rebase_requested.emit(
                row.commit_full_hash or row.commit_short_hash
            )
        )

        reset_menu = menu.addMenu("Reset current branch to this commit")
        for label, mode_id in (("Soft", "soft"), ("Mixed", "mixed"), ("Hard", "hard")):
            reset_action = reset_menu.addAction(label)
            reset_action.triggered.connect(
                lambda _checked=False, mode=mode_id: self.reset_to_commit_requested.emit(
                    row.commit_short_hash, mode
                )
            )

        menu.addSeparator()

        copy_short_action = menu.addAction("Copy SHA")
        copy_short_action.triggered.connect(
            lambda _checked=False, sha=row.commit_short_hash: self._copy_to_clipboard(sha)
        )

        copy_full_action = menu.addAction("Copy full hash")
        copy_full_action.triggered.connect(
            lambda _checked=False, sha=row.commit_short_hash: self._copy_full_hash(sha)
        )

        copy_msg_action = menu.addAction("Copy commit message")
        copy_msg_action.triggered.connect(
            lambda _checked=False, summary=row.summary: self._copy_to_clipboard(summary)
        )

        menu.exec(self._table.viewport().mapToGlobal(position))

    def _on_header_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self._table)
        for col in range(HistoryTableModel.column_count()):
            action = menu.addAction(HistoryTableModel.header_label(col))
            action.setCheckable(True)
            action.setChecked(not self._table.isColumnHidden(col))
            action.toggled.connect(
                lambda checked, column=col: self._toggle_column_and_sync(column, checked)
            )
        hdr = self._table.horizontalHeader()
        menu.exec(hdr.mapToGlobal(position))

    def _toggle_column_and_sync(self, column: int, visible: bool) -> None:
        self._set_column_visible(column, visible)
        action = self._column_actions.get(column)
        if action is not None:
            action.setChecked(visible)

    def _copy_to_clipboard(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _copy_full_hash(self, short_hash: str) -> None:
        if self._history_service is None or self._repository_path is None:
            self._copy_to_clipboard(short_hash)
            return
        row = next(
            (row for row in self._model.rows() if row.commit_short_hash == short_hash),
            None,
        )
        if row is not None and row.commit_full_hash:
            self._copy_to_clipboard(row.commit_full_hash)
            return
        runner = getattr(self._history_service, "_runner", None)
        path = self._repository_path
        if runner is None:
            return
        self._jobs.read(
            "copy_hash",
            lambda: runner.run(["rev-parse", short_hash], cwd=path).stdout.strip(),
            self._copy_to_clipboard,
        )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def set_history_service(self, service: HistoryService | None) -> None:
        self._history_service = service

    def set_worker_pool(self, pool: WorkerPool | None) -> None:
        self._worker_pool = pool
        if pool is not None:
            self._jobs.pool = pool

    def set_known_remotes(self, remotes: Sequence[str]) -> None:
        self._known_remotes = tuple(sorted({r for r in remotes if r}))
        self._model.set_known_remotes(self._known_remotes)

    def set_row_alpha(self, alpha: float) -> None:
        """Set the alternating-row transparency (0.0..1.0) and restyle."""

        self._row_alpha = max(0.0, min(1.0, float(alpha)))
        self._apply_row_alpha_stylesheet()

    def _apply_row_alpha_stylesheet(self) -> None:
        a = max(0, min(255, int(round(self._row_alpha * 255))))
        # Paint the alternating background on each item explicitly. The
        # viewport is non-auto-fill so a transparent main bg drops through to
        # the parent; setting the item bg per-row guarantees both base and
        # alternate rows get their share of the alpha-blended fill.
        self._table.setStyleSheet("""
            QTableView {
                background: transparent;
                alternate-background-color: rgba(26, 26, 46, %(a)d);
                gridline-color: transparent;
            }
            QTableView::item { padding: 3px 6px; background: rgba(30, 30, 46, %(a)d); }
            QTableView::item:alternate { background: rgba(26, 26, 46, %(a)d); }
            QTableView::item:selected { background: rgba(69, 71, 90, 220); }
            """ % {"a": a})
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.setAutoFillBackground(False)

    def load_repository(self, repository_path: str) -> None:
        self._jobs.activate(repository_path)
        same_repo = repository_path == self._repository_path
        self._repository_path = repository_path
        self._has_more = False
        if not same_repo:
            self._current_limit = self._INITIAL_LIMIT
            # Only reset ref selection when switching repositories.
            self._selected_refs = set()
            self._all_known_refs = set()
        self._refresh_graph()

    def refresh(self) -> None:
        self._refresh_graph()

    def invalidate_jobs(self) -> None:
        self._jobs.invalidate_reads()
        self._loading = False

    def clear(self) -> None:
        self.invalidate_jobs()
        self._model.clear()
        self._all_known_refs = set()
        self._rebuild_branch_menu()
        self._show_details(None)

    @property
    def model(self) -> HistoryTableModel:
        return self._model

    @property
    def proxy(self) -> HistoryFilterProxyModel:
        return self._proxy

    # -----------------------------------------------------------------------
    # Data loading
    # -----------------------------------------------------------------------

    def _refresh_graph(self) -> None:
        if self._history_service is None or self._repository_path is None:
            return
        service = self._history_service
        path = self._repository_path
        limit = self._current_limit
        refs = sorted(self._selected_refs)

        self._loading = True
        self._jobs.invalidate_reads()

        def run():
            if hasattr(service, "invalidate_repository"):
                service.invalidate_repository(path)
            tips = service.get_tips(path, refs or None)
            return tips, service.get_graph(path, limit=limit, refs=tips) if tips else []

        def apply(result):
            self._history_tips, rows = result
            self._apply_rows(rows, limit)

        self._jobs.read("history", run, apply, self._history_error)

    def _history_error(self, value) -> None:
        self._loading = False
        self._details_empty.setText(f"History unavailable: {value}")
        self._details_empty.show()

    def _apply_rows(self, rows: list[HistoryGraphRow], requested_limit: int) -> None:
        selected = self._table.currentIndex()
        source = self._proxy.mapToSource(selected)
        previous = (
            self._model.rows()[source.row()].commit_full_hash
            if source.isValid() and source.row() < len(self._model.rows())
            else None
        )
        scroll = self._table.verticalScrollBar().value()
        self._model.set_rows(rows)
        if previous:
            for index, row in enumerate(rows):
                if row.commit_full_hash == previous:
                    self._table.setCurrentIndex(
                        self._proxy.mapFromSource(self._model.index(index, 0))
                    )
                    break
        self._table.verticalScrollBar().setValue(scroll)
        self._has_more = len(rows) >= requested_limit
        self._loading = False
        self._update_known_refs(rows)
        self._rebuild_branch_menu()

    def _update_known_refs(self, rows: list[HistoryGraphRow]) -> None:
        """Update the cached union of refs seen for this repository.

        When no branch filter is active we can fully replace the set so that
        branches deleted in git eventually disappear from the menu. While a
        branch filter is active we only union in any new refs we happen to
        see, because the filtered result is a subset of the true ref set.
        """

        observed: set[str] = {ref for row in rows for ref in row.refs if ref}
        if not self._selected_refs:
            self._all_known_refs = observed
        else:
            self._all_known_refs.update(observed)

    def _load_more(self) -> None:
        if self._loading or not self._has_more:
            return
        if self._history_service is None or self._repository_path is None:
            return
        # Read only the next page against immutable tips, preserving graph continuity
        # by rebuilding the inexpensive in-memory layout over the combined rows.
        if len(self._model.rows()) >= 5000:
            self._has_more = False
            self._details_empty.setText(
                "History is limited to 5,000 rows. Narrow the branch filter to continue."
            )
            self._details_empty.show()
            return
        service, path = self._history_service, self._repository_path
        tips = getattr(self, "_history_tips", ())
        skip = len(self._model.rows())
        previous = self._model.rows()
        self._loading = True

        def apply(rows):
            self._current_limit = skip + self._PAGE_SIZE
            self._apply_rows(previous + rows, self._current_limit)

        self._jobs.read(
            "history",
            lambda: service.get_graph(path, limit=self._PAGE_SIZE, skip=skip, refs=tips),
            apply,
            self._history_error,
        )

    # -----------------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------------

    def _on_scroll_changed(self, value: int) -> None:
        scrollbar = self._table.verticalScrollBar()
        if scrollbar is None:
            return
        if self._loading or not self._has_more:
            return
        maximum = scrollbar.maximum()
        if maximum <= 0:
            return
        if value >= maximum - self._ROW_HEIGHT * 5:
            self._load_more()

    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self._jobs.invalidate_channel("commit_files")
        self._jobs.invalidate_channel("commit_diff")
        if not current.isValid():
            self._set_graph_highlight(None)
            self._show_details(None)
            return
        source_index = self._proxy.mapToSource(current)
        row = self._model.row_at(source_index.row())
        if row is None:
            self._set_graph_highlight(None)
            self._show_details(None)
            return
        layout = self._model.layout_at(source_index.row())
        highlighted_lane = (
            layout.node_color_lane if layout is not None and self._model.max_lanes() > 1 else None
        )
        self._set_graph_highlight(highlighted_lane)
        payload = self._model.data(
            self._model.index(source_index.row(), HistoryTableModel.COLUMN_GRAPH),
            GRAPH_ROW_ROLE,
        )
        branch_name = (
            payload.primary_ref or payload.inherited_ref
            if isinstance(payload, HistoryGraphPayload)
            else None
        )
        self._show_details(row, branch_name=branch_name)
        self.commit_selected.emit(row.commit_short_hash)

    def _set_graph_highlight(self, color_lane: int | None) -> None:
        if self._graph_delegate is None:
            return
        self._graph_delegate.set_highlighted_color_lane(color_lane)
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.update()

    def _on_filter_input_changed(self, text: str) -> None:
        normalized = text.lower().strip()
        if normalized == self._proxy.search_text():
            self._pending_search = None
            self._search_debounce.stop()
            return
        self._pending_search = normalized
        self._search_debounce.start()

    def _commit_search_change(self) -> None:
        if self._pending_search is None:
            return
        normalized = self._pending_search
        self._pending_search = None
        self._proxy.set_search(normalized)
        self._update_filter_summary()

    # -----------------------------------------------------------------------
    # Details panel
    # -----------------------------------------------------------------------

    def _show_details(
        self,
        row: HistoryGraphRow | None,
        *,
        branch_name: str | None = None,
    ) -> None:
        splitter_sizes = self._splitter.sizes()
        has_details = row is not None
        for widget in (
            self._details_hash,
            self._details_branch,
            self._details_author,
            self._details_date,
            self._details_refs,
            self._details_message,
            self._details_files,
            self._details_diff,
        ):
            widget.setVisible(has_details)
        self._details_empty.setVisible(not has_details)
        self._preserve_details_splitter_sizes(splitter_sizes)

        if row is None:
            self._current_commit_hash = None
            self._current_files = []
            self._details_files.clear()
            self._details_diff.clear()
            return

        self._details_hash.setText(f"Commit: {row.commit_short_hash}")
        resolved_branch = branch_name or row.head_ref or next(iter(row.refs), None)
        self._details_branch.setText(f"Branch: {resolved_branch or '(no branch)'}")
        self._details_author.setText(f"Author: {row.author}")
        if row.age:
            self._details_date.setText(f"Date: {row.date}  \u2022  {row.age}")
        else:
            self._details_date.setText(f"Date: {row.date}")

        if row.refs:
            ref_labels = []
            for ref in row.refs:
                color = ACCENT.name() if ref == row.head_ref else FG_SECONDARY.name()
                weight = " font-weight: 600;" if ref == row.head_ref else ""
                ref_labels.append(f'<span style="color: {color};{weight}">{ref}</span>')
            self._details_refs.setText("Refs: " + ", ".join(ref_labels))
            self._details_refs.setVisible(True)
        else:
            self._details_refs.setVisible(False)

        self._details_message.setText(row.summary)

        self._current_commit_hash = row.commit_full_hash or row.commit_short_hash
        self._load_commit_files(self._current_commit_hash)

    def _preserve_details_splitter_sizes(self, sizes: list[int]) -> None:
        """Keep content changes from renegotiating the details pane width."""

        if len(sizes) != 2 or not all(size >= 0 for size in sizes):
            return
        self._pending_details_splitter_sizes = list(sizes)
        self._splitter.setSizes(sizes)
        self._details_splitter_restore.start(0)

    def _restore_details_splitter_sizes(self) -> None:
        sizes = self._pending_details_splitter_sizes
        self._pending_details_splitter_sizes = None
        if sizes is not None:
            self._splitter.setSizes(sizes)

    def _load_commit_files(self, commit_hash: str) -> None:
        self._details_files.clear()
        self._details_diff.clear()
        self._current_files = []
        if not commit_hash or self._history_service is None or self._repository_path is None:
            return
        service = self._history_service
        path = self._repository_path

        self._jobs.read(
            "commit_files",
            lambda: service.get_commit_files(path, commit_hash),
            lambda files: self._apply_commit_files(commit_hash, files),
            lambda value: self._details_diff.set_diff(None, [], str(value)),
        )

    def _apply_commit_files(
        self,
        commit_hash: str,
        files: list[CommitFileChange],
    ) -> None:
        if commit_hash != self._current_commit_hash:
            return
        self._current_files = files
        self._details_files.clear()
        for change in files:
            badge = f"[{change.status}] "
            display = f"{badge}{change.path}"
            if change.old_path and change.old_path != change.path:
                display = f"{badge}{change.old_path} \u2192 {change.path}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, change.path)
            fg = _status_color(change.status)
            if fg is not None:
                item.setForeground(fg)
            self._details_files.addItem(item)
        if self._details_files.count() > 0:
            self._details_files.setCurrentRow(0)

    def _on_file_selected(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self._details_diff.clear()
            return
        file_path = current.data(Qt.ItemDataRole.UserRole)
        if (
            not file_path
            or self._history_service is None
            or self._repository_path is None
            or self._current_commit_hash is None
        ):
            return
        service = self._history_service
        path = self._repository_path
        commit_hash = self._current_commit_hash

        self._details_diff.set_diff(file_path, [], "Loading diff…")

        def apply(hunks):
            if (
                commit_hash == self._current_commit_hash
                and current is self._details_files.currentItem()
            ):
                self._details_diff.set_diff(file_path, hunks)

        self._jobs.read(
            "commit_diff",
            lambda: service.get_commit_file_diff(path, commit_hash, file_path),
            apply,
            lambda value: self._details_diff.set_diff(file_path, [], str(value)),
        )


def _status_color(status: str) -> QColor | None:
    from gitqt.ui.theme import (
        STATUS_ADDED,
        STATUS_DELETED,
        STATUS_MODIFIED,
        STATUS_UNTRACKED,
    )

    mapping = {
        "A": STATUS_ADDED,
        "M": STATUS_MODIFIED,
        "D": STATUS_DELETED,
        "R": STATUS_MODIFIED,
        "C": STATUS_MODIFIED,
        "T": STATUS_MODIFIED,
        "U": STATUS_UNTRACKED,
    }
    return mapping.get(status)
