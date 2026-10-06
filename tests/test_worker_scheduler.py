from __future__ import annotations

from gitqt.services.worker import WorkerScheduler


def test_worker_scheduler_cancels_superseded_task_for_same_key() -> None:
    scheduler = WorkerScheduler()
    run_markers: list[str] = []

    first = scheduler.schedule("repo:/tmp/repo", lambda: run_markers.append("first"))
    second = scheduler.schedule("repo:/tmp/repo", lambda: run_markers.append("second"))

    first_result = scheduler.execute(first)
    second_result = scheduler.execute(second)

    assert first.cancelled
    assert not first_result.executed
    assert first_result.cancelled
    assert second_result.executed
    assert second_result.should_apply
    assert run_markers == ["second"]


def test_worker_scheduler_supports_explicit_cancellation() -> None:
    scheduler = WorkerScheduler()
    run_markers: list[str] = []
    task = scheduler.schedule("repo:/tmp/repo", lambda: run_markers.append("run"))

    cancelled = scheduler.cancel(task)
    result = scheduler.execute(task)

    assert cancelled
    assert not result.executed
    assert result.cancelled
    assert run_markers == []


def test_worker_scheduler_rejects_stale_task_results() -> None:
    scheduler = WorkerScheduler()
    run_markers: list[str] = []
    stale_task = scheduler.schedule("repo:/tmp/repo", lambda: run_markers.append("stale"))
    fresh_task = scheduler.schedule("repo:/tmp/repo", lambda: run_markers.append("fresh"))

    stale_result = scheduler.execute(stale_task)
    fresh_result = scheduler.execute(fresh_task)

    assert not stale_result.executed
    assert not stale_result.should_apply
    assert fresh_result.executed
    assert fresh_result.should_apply
    assert run_markers == ["fresh"]
