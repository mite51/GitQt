"""Left sidebar for repository navigation: branches, tags, stashes, remotes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
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
    ACCENT,
    FG_SECONDARY,
    ui_font,
)


class Sidebar(QWidget):
    """Persistent left sidebar with branches, tags, stashes, and remotes."""

    manage_ref_requested = Signal(str, str)
    branch_checkout_requested = Signal(str)
    branch_create_requested = Signal()
    branch_delete_requested = Signal(str)
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
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

        self._local_root.takeChildren()
        head_name = tracking.branch_name
        for branch in local:
            self._insert_local_branch(branch, head_name)

        self._remote_root.takeChildren()
        for branch in remote:
            self._insert_remote_branch(branch)

        self._local_root.setExpanded(True)

        ahead_behind = ""
        if tracking.branch_name and not tracking.is_detached_head:
            parts = []
            if tracking.ahead:
                parts.append(f"\u2191{tracking.ahead}")
            if tracking.behind:
                parts.append(f"\u2193{tracking.behind}")
            if parts:
                ahead_behind = f"  ({' '.join(parts)})"
        self._branches_root.setText(0, f"Branches{ahead_behind}")

    def _insert_local_branch(
        self,
        branch: LocalBranchRef,
        head_name: str | None,
    ) -> None:
        if self._local_root is None:
            return
        segments = (
            branch.name.split("/")
            if self._split_branch_paths and "/" in branch.name
            else [branch.name]
        )
        leaf = self._ensure_path(self._local_root, segments)
        label = segments[-1]
        if branch.is_head:
            label = f"* {label}"
        leaf.setText(0, label)
        leaf.setData(0, Qt.ItemDataRole.UserRole, ("local_branch", branch.name))
        if branch.is_head:
            leaf.setForeground(0, ACCENT)
            leaf.setFont(0, ui_font(10, bold=True))
        if branch.upstream:
            leaf.setToolTip(0, f"{branch.name} (tracks {branch.upstream})")
        else:
            leaf.setToolTip(0, branch.name)

    def _insert_remote_branch(self, branch: RemoteBranchRef) -> None:
        if self._remote_root is None:
            return
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
                delete_action.setEnabled(not item.text(0).startswith("*"))
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

        if menu.actions():
            self._tree.viewport()
            menu.exec(self._tree.mapToGlobal(pos))


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
