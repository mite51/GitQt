"""Left sidebar for repository navigation: branches, tags, stashes, remotes."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QModelIndex, Qt, Signal
from PySide6.QtGui import QDrag, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gitqt.parsers.refs import LocalBranchRef, RemoteBranchRef
from gitqt.services.branch import BranchSnapshot, BranchTrackingStatus
from gitqt.services.process_context import redact
from gitqt.services.remote import RemoteInfo
from gitqt.services.stash import StashEntry
from gitqt.services.tag import TagInfo
from gitqt.ui.theme import (
    FG_SECONDARY,
    active_branch_color,
    ui_font,
)

ACTIVE_BRANCH_ROLE = Qt.ItemDataRole.UserRole + 1


class _BranchTree(QTreeWidget):
    """Branch drops request an operation; they never move navigation items."""

    branch_dropped = Signal(str, str)
    MIME = "application/x-gitqt-local-branch"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def startDrag(self, _actions):
        item = self.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data or data[0] != "local_branch":
            return
        mime = QMimeData()
        mime.setData(self.MIME, data[1].encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _drop_pair(self, event):
        if event.source() is not self or not event.mimeData().hasFormat(self.MIME):
            return None
        item = self.itemAt(event.position().toPoint())
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data or data[0] not in {"local_branch", "remote_branch"}:
            return None
        source = bytes(event.mimeData().data(self.MIME)).decode("utf-8")
        kind, name = data
        if (kind == "local_branch" and source == name) or name.endswith("/HEAD"):
            return None
        target = f"refs/{'heads' if kind == 'local_branch' else 'remotes'}/{name}"
        return source, target

    def dragEnterEvent(self, event):
        if event.source() is self and event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._drop_pair(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        pair = self._drop_pair(event)
        if pair:
            event.acceptProposedAction()
            self.branch_dropped.emit(*pair)
        else:
            event.ignore()


class _BranchItemDelegate(QStyledItemDelegate):
    """Keep the current branch color visible through tree selection."""

    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().initStyleOption(option, index)
        if not index.data(ACTIVE_BRANCH_ROLE):
            return
        palette = option.widget.palette() if option.widget is not None else option.palette
        color = active_branch_color(palette)
        option.palette.setColor(QPalette.ColorRole.Text, color)
        option.palette.setColor(QPalette.ColorRole.HighlightedText, color)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if not index.data(ACTIVE_BRANCH_ROLE):
            super().paint(painter, option, index)
            return

        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ""
        widget = styled.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, widget)

        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            styled,
            widget,
        )
        visible_text = styled.fontMetrics.elidedText(
            text,
            styled.textElideMode,
            text_rect.width(),
        )
        painter.save()
        painter.setFont(styled.font)
        painter.setPen(active_branch_color(widget.palette() if widget is not None else None))
        painter.drawText(text_rect, styled.displayAlignment, visible_text)
        painter.restore()


class Sidebar(QWidget):
    """Persistent left sidebar with branches, tags, stashes, and remotes."""

    manage_ref_requested = Signal(str, str)
    branch_checkout_requested = Signal(str)
    branch_create_requested = Signal()
    branch_delete_requested = Signal(str)
    rebase_requested = Signal(str, str, bool)
    stash_apply_requested = Signal(str)
    stash_pop_requested = Signal(str)
    stash_drop_requested = Signal(str)
    stash_create_requested = Signal()
    tag_create_requested = Signal()
    remote_fetch_requested = Signal(str)
    remote_push_requested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        split_branch_paths: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setMaximumWidth(360)

        self._split_branch_paths = split_branch_paths
        self._latest_snapshot: BranchSnapshot | None = None
        self._selected_head_name: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tree = _BranchTree()
        self._tree.branch_dropped.connect(self._branch_drop_menu)
        self._tree.setHeaderHidden(True)
        self._tree.setItemDelegate(_BranchItemDelegate(self._tree))
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.setStyleSheet(
            "QTreeWidget { border: none; border-right: 1px solid #45475a; border-radius: 0; }"
        )
        layout.addWidget(self._tree)

        self._branches_root: QTreeWidgetItem | None = None
        self._local_root: QTreeWidgetItem | None = None
        self._remote_root: QTreeWidgetItem | None = None
        self._tags_root: QTreeWidgetItem | None = None
        self._stashes_root: QTreeWidgetItem | None = None
        self._remotes_root: QTreeWidgetItem | None = None

        self._build_tree_structure()

    def set_split_branch_paths(self, enabled: bool) -> None:
        if enabled == self._split_branch_paths:
            return
        self._split_branch_paths = enabled
        if self._latest_snapshot is not None:
            self.set_branch_snapshot(self._latest_snapshot)

    def set_branch_snapshot(self, snapshot: BranchSnapshot) -> None:
        self._latest_snapshot = snapshot
        self._populate_branches(
            snapshot.local_branches, snapshot.remote_branches, snapshot.tracking
        )

    def set_stashes(self, stashes: list[StashEntry]) -> None:
        if self._stashes_root is None:
            return
        self._stashes_root.takeChildren()
        for entry in stashes:
            item = QTreeWidgetItem([entry.description or entry.ref])
            item.setData(0, Qt.ItemDataRole.UserRole, ("stash", entry.ref))
            item.setToolTip(0, entry.ref)
            self._stashes_root.addChild(item)
        self._stashes_root.setText(0, f"Stashes ({len(stashes)})")

    def set_tags(self, tags: list[TagInfo]) -> None:
        if self._tags_root is None:
            return
        self._tags_root.takeChildren()
        for tag in tags:
            item = QTreeWidgetItem([tag.name])
            item.setData(0, Qt.ItemDataRole.UserRole, ("tag", tag.name))
            self._tags_root.addChild(item)
        self._tags_root.setText(0, f"Tags ({len(tags)})")

    def set_remotes(self, remotes: list[RemoteInfo]) -> None:
        if self._remotes_root is None:
            return
        self._remotes_root.takeChildren()
        for remote in remotes:
            item = QTreeWidgetItem([remote.name])
            item.setData(0, Qt.ItemDataRole.UserRole, ("remote", remote.name))
            item.setToolTip(0, redact(f"Fetch: {remote.fetch_url}\nPush: {remote.push_url}"))
            self._remotes_root.addChild(item)
        self._remotes_root.setText(0, f"Remotes ({len(remotes)})")

    def clear_all(self) -> None:
        self._selected_head_name = None
        for root in (
            self._local_root,
            self._remote_root,
            self._stashes_root,
            self._tags_root,
            self._remotes_root,
        ):
            if root:
                root.takeChildren()

    def _build_tree_structure(self) -> None:
        self._tree.clear()

        self._branches_root = QTreeWidgetItem(["Branches"])
        self._branches_root.setFont(0, ui_font(11, bold=True))
        self._branches_root.setFlags(self._branches_root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._tree.addTopLevelItem(self._branches_root)

        self._local_root = QTreeWidgetItem(["Local"])
        self._local_root.setForeground(0, FG_SECONDARY)
        self._branches_root.addChild(self._local_root)

        self._remote_root = QTreeWidgetItem(["Remote"])
        self._remote_root.setForeground(0, FG_SECONDARY)
        self._branches_root.addChild(self._remote_root)

        self._branches_root.setExpanded(True)
        self._local_root.setExpanded(True)

        self._tags_root = QTreeWidgetItem(["Tags"])
        self._tags_root.setFont(0, ui_font(11, bold=True))
        self._tags_root.setFlags(self._tags_root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._tree.addTopLevelItem(self._tags_root)

        self._stashes_root = QTreeWidgetItem(["Stashes"])
        self._stashes_root.setFont(0, ui_font(11, bold=True))
        self._stashes_root.setFlags(self._stashes_root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._tree.addTopLevelItem(self._stashes_root)

        self._remotes_root = QTreeWidgetItem(["Remotes"])
        self._remotes_root.setFont(0, ui_font(11, bold=True))
        self._remotes_root.setFlags(self._remotes_root.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._tree.addTopLevelItem(self._remotes_root)

    def _populate_branches(
        self,
        local: list[LocalBranchRef],
        remote: list[RemoteBranchRef],
        tracking: BranchTrackingStatus,
    ) -> None:
        if self._local_root is None or self._remote_root is None:
            return

        selected = self._tree.currentItem()
        selected_data = selected.data(0, Qt.ItemDataRole.UserRole) if selected is not None else None
        rebuilt_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._local_root.takeChildren()
        head_name = tracking.branch_name
        active_item: QTreeWidgetItem | None = None
        for branch in local:
            item = self._insert_local_branch(branch, tracking)
            if item is not None:
                rebuilt_items[("local_branch", branch.name)] = item
            if branch.is_head:
                active_item = item

        self._remote_root.takeChildren()
        for branch in remote:
            item = self._insert_remote_branch(branch)
            if item is not None:
                rebuilt_items[("remote_branch", branch.name)] = item

        self._local_root.setExpanded(True)

        self._branches_root.setText(0, "Branches")

        item_to_select = None
        if active_item is not None and self._selected_head_name != head_name:
            item_to_select = active_item
        elif isinstance(selected_data, tuple):
            item_to_select = rebuilt_items.get(selected_data)

        if item_to_select is not None:
            parent = item_to_select.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self._tree.setCurrentItem(item_to_select)
            self._tree.scrollToItem(item_to_select)
        self._selected_head_name = head_name

    def _insert_local_branch(
        self,
        branch: LocalBranchRef,
        tracking: BranchTrackingStatus,
    ) -> QTreeWidgetItem | None:
        if self._local_root is None:
            return None
        segments = (
            branch.name.split("/")
            if self._split_branch_paths and "/" in branch.name
            else [branch.name]
        )
        leaf = self._ensure_path(self._local_root, segments)
        label = segments[-1]
        if branch.is_head:
            if branch.upstream:
                if tracking.ahead or tracking.behind:
                    parts = []
                    if tracking.ahead:
                        parts.append(f"↑{tracking.ahead}")
                    if tracking.behind:
                        parts.append(f"↓{tracking.behind}")
                    label = f"{label}  {'  '.join(parts)}"
                else:
                    label = f"{label}  Up to date"
        leaf.setText(0, label)
        leaf.setData(0, Qt.ItemDataRole.UserRole, ("local_branch", branch.name))
        leaf.setData(0, ACTIVE_BRANCH_ROLE, branch.is_head)
        if branch.is_head:
            leaf.setForeground(0, active_branch_color(self._tree.palette()))
            leaf.setFont(0, ui_font(10, bold=True))
        if branch.upstream:
            prefix = "Current branch: " if branch.is_head else ""
            leaf.setToolTip(0, f"{prefix}{branch.name} (tracks {branch.upstream})")
        else:
            leaf.setToolTip(0, f"Current branch: {branch.name}" if branch.is_head else branch.name)
        return leaf

    def _insert_remote_branch(self, branch: RemoteBranchRef) -> QTreeWidgetItem | None:
        if self._remote_root is None:
            return None
        segments = (
            branch.name.split("/")
            if self._split_branch_paths and "/" in branch.name
            else [branch.name]
        )
        leaf = self._ensure_path(self._remote_root, segments)
        leaf.setText(0, segments[-1])
        leaf.setData(0, Qt.ItemDataRole.UserRole, ("remote_branch", branch.name))
        leaf.setForeground(0, FG_SECONDARY)
        leaf.setToolTip(0, branch.name)
        return leaf

    def _ensure_path(
        self,
        root: QTreeWidgetItem,
        segments: list[str],
    ) -> QTreeWidgetItem:
        """Descend/create tree nodes for ``segments`` and return the leaf item."""

        parent = root
        for depth, segment in enumerate(segments[:-1]):
            existing = _find_group_child(parent, segment)
            if existing is None:
                group = QTreeWidgetItem([segment])
                group.setForeground(0, FG_SECONDARY)
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                group.setData(0, Qt.ItemDataRole.UserRole, ("branch_group", segment))
                parent.addChild(group)
                group.setExpanded(depth == 0)
                parent = group
            else:
                parent = existing
        leaf = QTreeWidgetItem([segments[-1]])
        parent.addChild(leaf)
        return leaf

    def _on_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, ref = data
        if kind == "local_branch":
            self.branch_checkout_requested.emit(ref)
        elif kind in {"remote_branch", "tag"}:
            self.manage_ref_requested.emit(kind, ref)

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)

        menu = QMenu(self)

        if data is None:
            text = item.text(0)
            if text.startswith("Branches"):
                menu.addAction("New Branch...", self.branch_create_requested.emit)
            elif text.startswith("Tags"):
                menu.addAction("New Tag...", self.tag_create_requested.emit)
            elif text.startswith("Stashes"):
                menu.addAction("Create Stash...", self.stash_create_requested.emit)
        else:
            kind, ref = data
            if kind == "local_branch":
                menu.addAction(f"Checkout {ref}", lambda: self.branch_checkout_requested.emit(ref))
                menu.addSeparator()
                menu.addAction("New Branch...", self.branch_create_requested.emit)
                menu.addAction(
                    "Rename / Set Upstream…", lambda: self.manage_ref_requested.emit(kind, ref)
                )
                menu.addSeparator()
                delete_action = menu.addAction(
                    f"Delete {ref}", lambda: self.branch_delete_requested.emit(ref)
                )
                current_branch = (
                    self._latest_snapshot.tracking.branch_name
                    if self._latest_snapshot is not None
                    else None
                )
                delete_action.setEnabled(ref != current_branch)
            elif kind == "stash":
                menu.addAction(f"Apply {ref}", lambda: self.stash_apply_requested.emit(ref))
                menu.addAction(f"Pop {ref}", lambda: self.stash_pop_requested.emit(ref))
                menu.addSeparator()
                menu.addAction(f"Drop {ref}", lambda: self.stash_drop_requested.emit(ref))
            elif kind == "remote":
                menu.addAction(f"Fetch {ref}", lambda: self.remote_fetch_requested.emit(ref))
                menu.addAction(f"Push to {ref}", lambda: self.remote_push_requested.emit(ref))
            elif kind in ("tag", "remote_branch"):
                menu.addAction(
                    "Manage / Inspect…", lambda: self.manage_ref_requested.emit(kind, ref)
                )

            if kind in {"local_branch", "remote_branch"} and not ref.endswith("/HEAD"):
                current = (
                    self._latest_snapshot.tracking.branch_name
                    if self._latest_snapshot is not None
                    else None
                )
                target = f"refs/{'heads' if kind == 'local_branch' else 'remotes'}/{ref}"
                menu.addSeparator()
                if kind == "local_branch":
                    menu.addAction(
                        f"Rebase {ref}…", lambda: self.rebase_requested.emit(ref, "", False)
                    )
                    menu.addAction(
                        f"Interactive rebase {ref}…",
                        lambda: self.rebase_requested.emit(ref, "", True),
                    )
                if current and (kind != "local_branch" or current != ref):
                    menu.addAction(
                        f"Rebase {current} onto {ref}…",
                        lambda: self.rebase_requested.emit(current, target, False),
                    )
                    menu.addAction(
                        f"Interactive rebase {current} onto {ref}…",
                        lambda: self.rebase_requested.emit(current, target, True),
                    )

        if menu.actions():
            self._tree.viewport()
            menu.exec(self._tree.mapToGlobal(pos))

    def _branch_drop_menu(self, source, target):
        from PySide6.QtGui import QCursor

        name = target.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        menu = QMenu(self)
        menu.addAction(
            f"Rebase {source} onto {name}…",
            lambda: self.rebase_requested.emit(source, target, False),
        )
        menu.addAction(
            f"Interactive rebase {source} onto {name}…",
            lambda: self.rebase_requested.emit(source, target, True),
        )
        menu.exec(QCursor.pos())


def _find_group_child(parent: QTreeWidgetItem, label: str) -> QTreeWidgetItem | None:
    for i in range(parent.childCount()):
        child = parent.child(i)
        if child is None:
            continue
        data = child.data(0, Qt.ItemDataRole.UserRole)
        if (
            isinstance(data, tuple)
            and data
            and data[0] == "branch_group"
            and child.text(0) == label
        ):
            return child
    return None
