from __future__ import annotations

from threading import Event, Thread

import pytest
from PySide6.QtWidgets import QApplication

from gitqt.services.session_jobs import SessionJobs
from gitqt.services.worker import WorkerExecutionResult, WorkerScheduler
from gitqt.services.worker_pool import WorkerError


class DeferredPool:
    def __init__(self):
        self.jobs = []

    def submit(self, *, key, run, on_result):
        self.jobs.append((key, run, on_result))

    def finish(self, index):
        key, run, callback = self.jobs[index]
        try:
            value = run()
        except Exception as exc:
            value = WorkerError(exc)
        callback(WorkerExecutionResult(index, key, True, False, True, value))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_navigation_drops_old_repository_results_and_errors(app):
    pool = DeferredPool()
    jobs = SessionJobs(pool)
    results, errors = [], []
    jobs.activate("repo-a")
    jobs.read("status", lambda: "a", results.append, errors.append)

    def fail():
        raise RuntimeError("repo a failed")

    jobs.read("history", fail, results.append, errors.append)
    jobs.activate("repo-b")
    jobs.read("status", lambda: "b", results.append, errors.append)
    pool.finish(2)
    pool.finish(0)
    pool.finish(1)
    assert results == ["b"] and errors == []


def test_latest_filter_wins_even_when_old_result_completes_last(app):
    pool = DeferredPool()
    jobs = SessionJobs(pool)
    results = []
    jobs.read("history", lambda: "old-filter", results.append)
    jobs.read("history", lambda: "new-filter", results.append)
    pool.finish(1)
    pool.finish(0)
    assert results == ["new-filter"]


def test_mutations_are_not_coalesced_or_overlapped_and_invalidate_reads(app):
    pool = DeferredPool()
    jobs = SessionJobs(pool)
    results, busy = [], []
    jobs.busy_changed.connect(busy.append)
    jobs.read("status", lambda: "before", results.append)
    assert jobs.write(lambda: "commit", results.append)
    assert not jobs.write(lambda: "checkout", results.append)
    assert not jobs.activate("different-repo")
    pool.finish(0)
    pool.finish(1)
    assert results == ["commit"]
    assert busy == [True, False]
    assert jobs.activate("different-repo")


def test_failed_mutation_releases_gate_and_runs_cleanup(app):
    pool = DeferredPool()
    jobs = SessionJobs(pool)
    errors, finished = [], []

    def fail():
        raise RuntimeError("failed")

    jobs.write(fail, lambda _: None, errors.append, lambda: finished.append(True))
    pool.finish(0)
    assert not jobs.busy and finished == [True]
    assert "failed" in str(errors[0])


def test_superseded_running_worker_does_not_remove_new_task():
    scheduler = WorkerScheduler()
    entered, release = Event(), Event()

    def slow():
        entered.set()
        assert release.wait(2)
        return "old"

    first = scheduler.schedule("status", slow)
    results = []
    thread = Thread(target=lambda: results.append(scheduler.execute(first)))
    thread.start()
    assert entered.wait(2)
    second = scheduler.schedule("status", lambda: "new")
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert not results[0].should_apply
    assert scheduler.execute(second).value == "new"
