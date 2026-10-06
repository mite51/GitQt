from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable


@dataclass(slots=True)
class ScheduledWorkerTask:
    operation_id: int
    key: str
    run: Callable[[], object]
    cancelled: bool = False


@dataclass(slots=True)
class WorkerExecutionResult:
    operation_id: int
    key: str
    executed: bool
    cancelled: bool
    should_apply: bool
    value: object | None = None


class WorkerScheduler:
    """Coalesce repeated jobs and drop stale or cancelled results."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._next_operation_id = 1
        self._active_by_key: dict[str, ScheduledWorkerTask] = {}
        self._latest_requested_by_key: dict[str, int] = {}

    def schedule(self, key: str, run: Callable[[], object]) -> ScheduledWorkerTask:
        with self._lock:
            prior_task = self._active_by_key.get(key)
            if prior_task is not None:
                prior_task.cancelled = True

            operation_id = self._next_operation_id
            self._next_operation_id += 1
            task = ScheduledWorkerTask(operation_id=operation_id, key=key, run=run)
            self._active_by_key[key] = task
            self._latest_requested_by_key[key] = operation_id
            return task

    def cancel(self, task: ScheduledWorkerTask) -> bool:
        with self._lock:
            if task.cancelled:
                return False
            active = self._active_by_key.get(task.key)
            if active is None or active.operation_id != task.operation_id:
                task.cancelled = True
                return False
            task.cancelled = True
            self._active_by_key.pop(task.key, None)
            return True

    def is_current(self, task_id: int, key: str) -> bool:
        with self._lock:
            return self._latest_requested_by_key.get(key) == task_id

    def forget(self, task_id: int, key: str) -> None:
        with self._lock:
            if self._latest_requested_by_key.get(key) == task_id:
                self._latest_requested_by_key.pop(key, None)

    def execute(self, task: ScheduledWorkerTask) -> WorkerExecutionResult:
        with self._lock:
            active = self._active_by_key.get(task.key)
            if task.cancelled or active is not task:
                task.cancelled = True
                return WorkerExecutionResult(task.operation_id, task.key, False, True, False, None)
        try:
            value = task.run()
        finally:
            # An older job must never remove a newer job scheduled in the same channel.
            with self._lock:
                if self._active_by_key.get(task.key) is task:
                    self._active_by_key.pop(task.key, None)
        with self._lock:
            current = not task.cancelled and self.is_current(task.operation_id, task.key)
        return WorkerExecutionResult(
            task.operation_id, task.key, True, task.cancelled, current, value
        )
