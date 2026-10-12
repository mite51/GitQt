"""Qt-aware worker pool that runs Git work off the UI thread.

Wraps :class:`WorkerScheduler` so that callers submit small `run` callables
keyed by logical operation (e.g. ``"history::/path/to/repo"``). Background
execution happens on a ``QThreadPool`` and results are delivered back on the
Qt main thread, guarded by the scheduler's coalescing + stale-suppression
semantics.

An ``ImmediateWorkerPool`` test double is provided so view-model tests can
keep running synchronously without spinning up Qt's event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from gitqt.services.worker import ScheduledWorkerTask, WorkerExecutionResult, WorkerScheduler


@dataclass(slots=True)
class WorkerError:
    """Wraps an exception raised on the worker thread for UI consumption."""

    exception: BaseException

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self.exception).__name__}: {self.exception}"


ResultCallback = Callable[[WorkerExecutionResult], None]


class _WorkerSignals(QObject):
    """Qt signal bridge for result delivery back on the main thread."""

    result_ready = Signal(object)


class _WorkerRunnable(QRunnable):
    """QRunnable that executes a scheduled task and emits the result."""

    def __init__(
        self,
        pool: "WorkerPool",
        task: ScheduledWorkerTask,
        signals: _WorkerSignals,
    ) -> None:
        super().__init__()
        self._pool = pool
        self._task = task
        self._signals = signals

    def run(self) -> None:  # pragma: no cover - exercised via pool tests
        try:
            result = self._pool._execute_task(self._task)
        except BaseException as exc:  # noqa: BLE001 - propagate as error value
            result = WorkerExecutionResult(
                operation_id=self._task.operation_id,
                key=self._task.key,
                executed=True,
                cancelled=False,
                should_apply=True,
                value=WorkerError(exception=exc),
            )
        self._signals.result_ready.emit(result)


class WorkerPool(QObject):
    """Submit background jobs and deliver coalesced results to the UI thread."""

    def __init__(
        self,
        *,
        scheduler: WorkerScheduler | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._scheduler = scheduler or WorkerScheduler()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._callbacks: dict[int, ResultCallback] = {}

    def submit(
        self,
        *,
        key: str,
        run: Callable[[], Any],
        on_result: ResultCallback,
    ) -> ScheduledWorkerTask:
        task = self._scheduler.schedule(key, run)
        self._callbacks[task.operation_id] = on_result

        signals = _WorkerSignals()
        signals.result_ready.connect(self._on_result_ready, Qt.ConnectionType.QueuedConnection)
        runnable = _WorkerRunnable(self, task, signals)
        try:
            self._thread_pool.start(runnable)
        except BaseException:
            self.cancel(task)
            raise
        return task

    def cancel(self, task: ScheduledWorkerTask) -> bool:
        cancelled = self._scheduler.cancel(task)
        self._callbacks.pop(task.operation_id, None)
        self._scheduler.forget(task.operation_id, task.key)
        return cancelled

    def wait_for_done(self, timeout_ms: int = -1) -> bool:
        return self._thread_pool.waitForDone(timeout_ms)

    def _execute_task(self, task: ScheduledWorkerTask) -> WorkerExecutionResult:
        return self._scheduler.execute(task)

    @Slot(object)
    def _on_result_ready(self, result: WorkerExecutionResult) -> None:
        callback = self._callbacks.pop(result.operation_id, None)
        try:
            if callback is None:
                return
            if not result.should_apply or not self._scheduler.is_current(
                result.operation_id, result.key
            ):
                return
            callback(result)
        finally:
            # Session generations and mutation IDs produce unique keys. Retain
            # identities only until delivery, and never remove a newer request.
            self._scheduler.forget(result.operation_id, result.key)


class ImmediateWorkerPool:
    """Synchronous worker pool test double.

    Runs each submitted callable inline and routes the result back through the
    scheduler so coalescing / stale-suppression remains observable in tests
    without needing a Qt event loop.
    """

    def __init__(self, scheduler: WorkerScheduler | None = None) -> None:
        self._scheduler = scheduler or WorkerScheduler()

    def submit(
        self,
        *,
        key: str,
        run: Callable[[], Any],
        on_result: ResultCallback,
    ) -> ScheduledWorkerTask:
        task = self._scheduler.schedule(key, run)
        try:
            result = self._scheduler.execute(task)
        except BaseException as exc:  # noqa: BLE001
            result = WorkerExecutionResult(
                operation_id=task.operation_id,
                key=task.key,
                executed=True,
                cancelled=False,
                should_apply=True,
                value=WorkerError(exception=exc),
            )
        try:
            if result.should_apply and self._scheduler.is_current(task.operation_id, key):
                on_result(result)
        finally:
            self._scheduler.forget(task.operation_id, key)
        return task

    def cancel(self, task: ScheduledWorkerTask) -> bool:
        cancelled = self._scheduler.cancel(task)
        self._scheduler.forget(task.operation_id, task.key)
        return cancelled

    def wait_for_done(self, timeout_ms: int = -1) -> bool:  # noqa: ARG002
        return True
