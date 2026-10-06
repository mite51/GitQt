"""Scope background work to one visible repository session.

Read results are accepted only for the latest request in their channel. Mutations
are never coalesced, and one mutation at a time owns the workspace until its
completion callback runs on the Qt thread.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import count
from pathlib import Path
from threading import RLock

from PySide6.QtCore import QObject, Signal
from shiboken6 import isValid

from gitqt.services.process_context import ProcessContext, process_context
from gitqt.services.worker_pool import WorkerError, WorkerPool

_REPOSITORY_LOCKS = defaultdict(RLock)


class SessionJobs(QObject):
    busy_changed = Signal(bool)
    progress = Signal(str)

    def __init__(self, pool=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = pool if pool is not None else WorkerPool(parent=self)
        self.context: str | None = None
        self.generation = 0
        self.busy = False
        self.cancellable = False
        self._process_context = None
        self._requests: dict[str, int] = {}
        self._ids = count(1)

    def activate(self, context: str | None) -> bool:
        if self.busy:
            return False
        self.context = context
        self.invalidate_reads()
        return True

    def invalidate_reads(self) -> None:
        self.generation += 1
        self._requests.clear()

    def invalidate_channel(self, channel):
        self._requests.pop(channel, None)

    def read(self, channel: str, run, apply, error=None) -> bool:
        if self.busy:
            return False
        context = self.context
        generation = self.generation
        request = next(self._ids)
        self._requests[channel] = request

        def complete(value):
            if (
                not isValid(self)
                or generation != self.generation
                or self._requests.get(channel) != request
            ):
                return
            if isinstance(value, WorkerError):
                if error is not None:
                    error(value)
            else:
                apply(value)

        def current_run():
            if generation != self.generation or self._requests.get(channel) != request:
                return None
            return run()

        try:
            self.pool.submit(
                key=f"session::{id(self)}::{generation}::{channel}",
                run=self._locked(context, current_run),
                on_result=lambda result: complete(result.value),
            )
        except Exception as exc:
            complete(WorkerError(exc))
        return True

    def write(self, run, apply, error=None, finished=None, *, cancellable=False) -> bool:
        if self.busy:
            return False
        self.invalidate_reads()
        context = self.context
        generation = self.generation
        self.busy = True
        self.cancellable = cancellable
        process = ProcessContext(progress=self.progress.emit)
        self._process_context = process
        self.busy_changed.emit(True)
        original_run = run

        def run():
            with process_context(process):
                return original_run()

        def complete(value):
            process.finished.set()
            if not isValid(self):
                return
            self.busy = False
            self.cancellable = False
            self._process_context = None
            self.busy_changed.emit(False)
            try:
                if generation == self.generation:
                    if isinstance(value, WorkerError):
                        if error is not None:
                            error(value)
                    else:
                        apply(value)
            finally:
                if finished is not None:
                    finished()

        try:
            self.pool.submit(
                key=f"mutation::{id(self)}::{next(self._ids)}",
                run=self._locked(context, run),
                on_result=lambda result: complete(result.value),
            )
        except Exception as exc:
            complete(WorkerError(exc))
        return True

    @staticmethod
    def _locked(context, run):
        def work():
            key = context
            if context is not None:
                root = Path(context).resolve()
                metadata = root / ".git"
                if metadata.is_file():
                    value = metadata.read_text().strip()
                    if value.startswith("gitdir: "):
                        metadata = (root / value[8:]).resolve()
                common = metadata / "commondir"
                if common.is_file():
                    metadata = (metadata / common.read_text().strip()).resolve()
                key = str(metadata.resolve())
            with _REPOSITORY_LOCKS[key]:
                return run()

        return work

    def cancel(self) -> None:
        if self.busy and self.cancellable and self._process_context is not None:
            self._process_context.cancel.set()
