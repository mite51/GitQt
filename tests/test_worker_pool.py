from __future__ import annotations

from gitqt.services.worker import WorkerExecutionResult, WorkerScheduler
from gitqt.services.worker_pool import ImmediateWorkerPool, WorkerError


def test_immediate_pool_delivers_result_to_callback_on_main_thread() -> None:
    pool = ImmediateWorkerPool()
    received: list[WorkerExecutionResult] = []

    pool.submit(
        key="test::a",
        run=lambda: "hello",
        on_result=received.append,
    )

    assert len(received) == 1
    assert received[0].executed
    assert received[0].should_apply
    assert received[0].value == "hello"


def test_immediate_pool_wraps_exceptions_into_worker_error() -> None:
    pool = ImmediateWorkerPool()
    received: list[WorkerExecutionResult] = []

    def _boom() -> str:
        raise RuntimeError("boom")

    pool.submit(
        key="test::b",
        run=_boom,
        on_result=received.append,
    )

    assert len(received) == 1
    value = received[0].value
    assert isinstance(value, WorkerError)
    assert isinstance(value.exception, RuntimeError)
    assert "boom" in str(value)


def test_immediate_pool_coalesces_duplicate_keys_and_suppresses_stale_results() -> None:
    scheduler = WorkerScheduler()
    pool = ImmediateWorkerPool(scheduler=scheduler)
    received: list[str] = []

    # Schedule two tasks for the same key without executing the first in
    # between. The first is superseded before execution and must not deliver.
    first_task = scheduler.schedule("history::/tmp/repo", lambda: received.append("first"))
    second_task = scheduler.schedule("history::/tmp/repo", lambda: received.append("second"))

    first_result = scheduler.execute(first_task)
    second_result = scheduler.execute(second_task)

    assert not first_result.executed
    assert first_result.cancelled
    assert not first_result.should_apply
    assert second_result.executed
    assert second_result.should_apply
    assert received == ["second"]

    # Freshly submitted single jobs through the pool should still deliver.
    delivered: list[WorkerExecutionResult] = []
    pool.submit(
        key="history::/tmp/repo",
        run=lambda: "value",
        on_result=delivered.append,
    )
    assert len(delivered) == 1
    assert delivered[0].value == "value"


def test_immediate_pool_cancel_prevents_callback() -> None:
    pool = ImmediateWorkerPool()
    received: list[str] = []

    task = pool._scheduler.schedule("cancel::key", lambda: received.append("ran"))
    cancelled = pool.cancel(task)

    assert cancelled
    assert received == []
