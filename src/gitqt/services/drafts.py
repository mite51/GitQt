"""Durable commit drafts, isolated by Git common directory and worktree."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock, Timer
from uuid import uuid4

from gitqt.services.settings import SettingsStore, locked_file


class DraftStore:
    """Coalesce typing in memory and persist on a background timer.

    Explicit flushes on navigation/exit are synchronous durability boundaries.
    A failed write leaves the pending draft available for the next retry.
    """

    def __init__(self, path: Path, *, debounce_seconds: float = 0.5) -> None:
        self.path = path
        self.debounce_seconds = debounce_seconds
        self.warning: str | None = None
        self.last_error: str | None = None
        self._pending: dict[str, tuple[str, str]] = {}
        self._known: dict[str, tuple[str, str]] = {}
        self._observed: dict[str, str | None] = {}
        self._mutex = RLock()
        self._flush_lock = RLock()
        self._timer: Timer | None = None

    @staticmethod
    def identity(repository_path: str) -> str:
        root = Path(repository_path).expanduser().resolve()
        git_dir = root / ".git"
        if git_dir.is_file():
            content = git_dir.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                git_dir = (root / content[len("gitdir:") :].strip()).resolve()
        common_dir = git_dir
        common_file = git_dir / "commondir"
        if common_file.is_file():
            common_dir = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
        identity = [os.path.normcase(str(path.resolve())) for path in (common_dir, git_dir, root)]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def load(self, identity: str) -> tuple[str, str]:
        with self._mutex:
            if identity in self._pending:
                return self._pending[identity]
        with locked_file(self.path):
            payload = self._load()
        value = payload.get(identity)
        if not isinstance(value, dict):
            value = {}
        draft = (
            value.get("summary", "") if isinstance(value.get("summary"), str) else "",
            value.get("body", "") if isinstance(value.get("body"), str) else "",
        )
        with self._mutex:
            self._known[identity] = draft
            self._observed[identity] = value.get("revision")
        return draft

    def stage(self, identity: str, summary: str, body: str) -> None:
        with self._mutex:
            value = (summary, body)
            if self._known.get(identity) == value:
                return
            self._known[identity] = value
            self._pending[identity] = value
            if self._timer is not None:
                self._timer.cancel()
            self._timer = Timer(self.debounce_seconds, self._background_flush)
            self._timer.daemon = True
            self._timer.start()

    def _background_flush(self) -> None:
        try:
            self.flush()
        except OSError:
            pass  # Retained for an explicit flush/retry; the UI observes last_error.

    def flush(self) -> None:
        try:
            self._flush_pending()
        except OSError as exc:
            self.last_error = str(exc)
            raise

    def _flush_pending(self) -> None:
        with self._flush_lock:
            with self._mutex:
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                pending = dict(self._pending)
                observed = dict(self._observed)
            if not pending:
                return
            with locked_file(self.path):
                payload = self._load()
                previous = dict(payload)
                accepted = set()
                for identity, (summary, body) in pending.items():
                    current = payload.get(identity, {})
                    if not isinstance(current, dict):
                        current = {}
                    if summary or body:
                        if (current.get("summary"), current.get("body")) == (summary, body):
                            accepted.add(identity)
                            continue
                        if current and current.get("revision") != observed.get(identity):
                            conflict = self.path.with_name(
                                f"{self.path.name}.conflict-{uuid4().hex}"
                            )
                            SettingsStore._atomic_write(
                                conflict, (json.dumps({identity: current}) + "\n").encode()
                            )
                            self.warning = f"Concurrent commit draft preserved at {conflict}."
                        accepted.add(identity)
                        payload[identity] = {
                            "summary": summary,
                            "body": body,
                            "revision": uuid4().hex,
                        }
                    elif current.get("revision") == observed.get(identity):
                        # Clearing an old composer must never delete another
                        # instance's newer draft for this worktree.
                        payload.pop(identity, None)
                        accepted.add(identity)
                if payload != previous:
                    if self.path.exists():
                        SettingsStore._atomic_write(
                            self.path.with_suffix(self.path.suffix + ".bak"), self.path.read_bytes()
                        )
                    SettingsStore._atomic_write(
                        self.path,
                        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(),
                    )
            with self._mutex:
                for identity, draft in pending.items():
                    if identity in accepted:
                        self._observed[identity] = payload.get(identity, {}).get("revision")
                    if self._pending.get(identity) == draft:
                        self._pending.pop(identity)
                self.last_error = None

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_bytes())
            if isinstance(payload, dict):
                return payload
        except (ValueError, UnicodeError):
            pass
        damaged = self.path.with_name(f"{self.path.name}.corrupt-{uuid4().hex[:8]}")
        os.replace(self.path, damaged)
        self.warning = f"Damaged commit drafts saved to {damaged.name}."
        try:
            content = self.path.with_suffix(self.path.suffix + ".bak").read_bytes()
            payload = json.loads(content)
            if isinstance(payload, dict):
                SettingsStore._atomic_write(self.path, content)
                return payload
        except (OSError, ValueError, UnicodeError):
            pass
        return {}
