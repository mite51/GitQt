"""Long-session and failed-submission acceptance for real Qt worker delivery."""

from threading import Event

import pytest
from PySide6.QtCore import QThreadPool

from gitqt.services.worker_pool import WorkerPool


def test_completed_session_jobs_release_scheduler_bookkeeping(qtbot):
    pool = WorkerPool()
    delivered = []
    for index in range(100):
        pool.submit(key=f"session::{index}::status", run=lambda: True, on_result=delivered.append)
    qtbot.waitUntil(lambda: len(delivered) == 100, timeout=5000)
    assert pool.wait_for_done(1000)
    assert not pool._callbacks
    assert not pool._scheduler._active_by_key
    assert not pool._scheduler._latest_requested_by_key


def test_failed_submission_releases_scheduler_and_allows_retry(qtbot):
    class RejectOnce:
        def __init__(self):
            self.failed = False

        def start(self, runnable):
            if not self.failed:
                self.failed = True
                raise RuntimeError("Worker submission failed")
            QThreadPool.globalInstance().start(runnable)

    pool = WorkerPool(thread_pool=RejectOnce())
    delivered = []
    with pytest.raises(RuntimeError, match="submission"):
        pool.submit(key="status", run=lambda: True, on_result=delivered.append)
    assert not pool._callbacks
    assert not pool._scheduler._active_by_key
    assert not pool._scheduler._latest_requested_by_key
    pool.submit(key="status", run=lambda: "retried", on_result=delivered.append)
    qtbot.waitUntil(lambda: len(delivered) == 1)
    assert delivered[0].value == "retried"


def test_late_error_cannot_revive_superseded_request_after_new_delivery(qtbot):
    threads = QThreadPool()
    threads.setMaxThreadCount(2)
    pool = WorkerPool(thread_pool=threads)
    entered, release = Event(), Event()
    delivered = []

    def fail_late():
        entered.set()
        assert release.wait(5)
        raise RuntimeError("Old failure")

    try:
        pool.submit(key="history", run=fail_late, on_result=delivered.append)
        qtbot.waitUntil(entered.is_set)
        pool.submit(key="history", run=lambda: "latest", on_result=delivered.append)
        qtbot.waitUntil(lambda: len(delivered) == 1)
        assert not pool._scheduler._latest_requested_by_key
    finally:
        release.set()
        assert pool.wait_for_done(5000)
    qtbot.waitUntil(lambda: not pool._callbacks)
    assert [result.value for result in delivered] == ["latest"]
