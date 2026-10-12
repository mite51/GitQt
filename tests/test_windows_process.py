"""Windows ownership contracts plus native tests collected by the Windows CI jobs."""

import ctypes
import io
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from gitqt.services.git import (
    GitCommandCancelledError,
    GitCommandTimeoutError,
    GitExecutable,
    GitRunner,
)
from gitqt.services.process_context import ProcessContext, process_context
from gitqt.services.windows_process import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_SUSPENDED,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    WindowsAPI,
    WindowsJob,
    _Accounting,
    _ExtendedLimits,
    _ThreadEntry,
    owned_windows_process,
)


class FakeAPI:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def record(self, name, *args):
        self.calls.append((name, *args))
        if self.fail == name:
            raise OSError(f"injected {name} failure")

    def create_job(self):
        self.record("create")
        return 123

    def configure(self, job):
        self.record("configure", job)

    def assign(self, job, pid):
        self.record("assign", job, pid)

    def resume(self, pid):
        self.record("resume", pid)

    def terminate(self, job):
        self.record("terminate", job)

    def active_processes(self, job):
        self.record("active", job)
        return 0

    def close_handle(self, handle):
        self.record("close", handle)


class FakeProcess:
    pid = 456

    def __init__(self):
        self.returncode = None
        self.killed = False
        self.waited = False
        self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = 1

    def wait(self):
        self.waited = True
        return self.returncode


def test_windows_abi_layouts():
    assert ctypes.sizeof(_ThreadEntry) == 28
    assert ctypes.sizeof(_Accounting) == 48
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(_ExtendedLimits) == 144


def test_job_creation_is_noninheritable_and_disallows_breakaway():
    api = WindowsAPI.__new__(WindowsAPI)
    observed = []

    def configure(job, information_class, pointer, size):
        observed.append(
            (job, information_class, pointer._obj.BasicLimitInformation.LimitFlags, size)
        )
        return 1

    api.kernel = SimpleNamespace(
        CreateJobObjectW=lambda security, name: observed.append((security, name)) or 123,
        SetInformationJobObject=configure,
    )
    job = api.create_job()
    api.configure(job)
    assert observed == [
        (None, None),
        (123, 9, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, ctypes.sizeof(_ExtendedLimits)),
    ]


def test_assignment_failure_closes_reopened_process_handle(monkeypatch):
    api = WindowsAPI.__new__(WindowsAPI)
    closed = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "denied"), raising=False)
    api.kernel = SimpleNamespace(
        OpenProcess=lambda rights, inherit, pid: 999,
        AssignProcessToJobObject=lambda job, process: 0,
        CloseHandle=lambda handle: closed.append(handle) or 1,
    )
    with pytest.raises(OSError, match="denied"):
        api.assign(123, 456)
    assert closed == [999]


@pytest.mark.parametrize("failure", [None, "snapshot", "open-thread", "resume", "suspend-count"])
def test_initial_thread_handles_close_on_every_resume_path(monkeypatch, failure):
    api = WindowsAPI.__new__(WindowsAPI)
    closed = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 18, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "injected"), raising=False)

    def first(_snapshot, pointer):
        pointer._obj.th32OwnerProcessID = 456
        pointer._obj.th32ThreadID = 789
        return 1

    api.kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=lambda flags, pid: (
            ctypes.c_void_p(-1).value if failure == "snapshot" else 111
        ),
        Thread32First=first,
        Thread32Next=lambda snapshot, entry: 0,
        OpenThread=lambda rights, inherit, tid: 0 if failure == "open-thread" else 222,
        ResumeThread=lambda thread: (
            0xFFFFFFFF if failure == "resume" else 2 if failure == "suspend-count" else 1
        ),
        CloseHandle=lambda handle: closed.append(handle) or 1,
    )
    if failure:
        with pytest.raises(OSError):
            api.resume(456)
    else:
        api.resume(456)
    expected = [] if failure == "snapshot" else [111] if failure == "open-thread" else [222, 111]
    assert closed == expected


def test_command_is_owned_before_initial_thread_resumes_and_cleanup_is_idempotent():
    api = FakeAPI()
    process = FakeProcess()

    def popen(*_args, **options):
        assert options["creationflags"] == CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP
        api.record("launch")
        return process

    with owned_windows_process(["command"], api=api, popen=popen) as (owned, job):
        assert owned is process
        assert [call[0] for call in api.calls] == [
            "create",
            "configure",
            "launch",
            "assign",
            "resume",
        ]
        job.close()
    assert [call[0] for call in api.calls].count("terminate") == 1
    assert [call[0] for call in api.calls].count("close") == 1
    assert process.killed and process.waited
    assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))


@pytest.mark.parametrize(
    "failure", ["create", "configure", "launch", "assign", "resume", "capture"]
)
def test_setup_and_capture_failures_never_release_a_running_unowned_process(failure):
    api = FakeAPI(fail=failure)
    process = FakeProcess()
    launched = []

    def popen(*_args, **_kwargs):
        api.record("launch")
        launched.append(process)
        return process

    with pytest.raises(OSError, match=f"injected {failure} failure"):
        with owned_windows_process(["command"], api=api, popen=popen):
            api.record("capture")
    if failure != "create":
        assert ("close", 123) in api.calls
    if failure in {"create", "configure", "launch", "assign"}:
        assert ("resume", process.pid) not in api.calls
    if launched:
        assert process.killed and process.waited
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))


@pytest.mark.parametrize("failure", ["terminate", "active"])
def test_cleanup_failure_still_closes_kill_on_close_handle_and_reports_error(failure):
    api = FakeAPI(fail=failure)
    process = FakeProcess()
    with pytest.raises(OSError, match=f"injected {failure} failure"):
        with owned_windows_process(["command"], api=api, popen=lambda *a, **k: process):
            pass
    assert api.calls[-1] == ("close", 123)
    assert process.killed and process.waited


def test_cleanup_wait_has_a_bound_and_closes_job(monkeypatch):
    api = FakeAPI()
    api.active_processes = lambda _job: 1
    job = WindowsJob(api)
    monkeypatch.setattr("gitqt.services.windows_process.time.monotonic", iter([0, 11]).__next__)
    with pytest.raises(OSError, match="did not confirm command-tree termination"):
        job.close()
    assert api.calls[-1] == ("close", 123)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows Job Objects")
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
def test_native_job_stops_child_after_parent_exits(tmp_path, monkeypatch, interruption):
    import gitqt.services.windows_process as windows_process

    ready = tmp_path / "child-ready"
    late_write = tmp_path / "unexpected-write"
    child_code = (
        "import time; from pathlib import Path;"
        f" Path({str(ready)!r}).write_text('ready'); time.sleep(3);"
        f" Path({str(late_write)!r}).write_text('orphaned mutation')"
    )
    parent_code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_code!r}])"
    real_popen = subprocess.Popen
    parents = []

    def capture_parent(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        parents.append(process)
        return process

    monkeypatch.setattr(windows_process.subprocess, "Popen", capture_parent)
    context = ProcessContext()
    failures = []
    runner = GitRunner(GitExecutable(sys.executable))

    def work():
        with process_context(context):
            try:
                runner.run(
                    ["-c", parent_code], timeout_seconds=1.5 if interruption == "timeout" else 10
                )
            except Exception as error:
                failures.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    try:
        deadline = time.monotonic() + 1.3
        while time.monotonic() < deadline:
            if parents and parents[0].poll() is not None and ready.exists():
                break
            time.sleep(0.01)
        assert parents and parents[0].poll() == 0 and ready.exists()
        assert worker.is_alive(), "The operation must still own the surviving child."
        if interruption == "cancel":
            context.cancel.set()
        worker.join(12)
        assert not worker.is_alive()
        expected = GitCommandTimeoutError if interruption == "timeout" else GitCommandCancelledError
        assert len(failures) == 1 and isinstance(failures[0], expected), failures
        time.sleep(3)
        assert not late_write.exists()
        assert runner.run(["-c", "print('retry')"]).stdout == "retry\n"
    finally:
        context.cancel.set()
        worker.join(12)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows Job Objects")
def test_native_success_closes_detached_command_descendants(tmp_path):
    ready = tmp_path / "ready"
    late_write = tmp_path / "unexpected-write"
    child_code = (
        "import time; from pathlib import Path;"
        f" Path({str(ready)!r}).write_text('ready'); time.sleep(2);"
        f" Path({str(late_write)!r}).write_text('unexpected')"
    )
    parent_code = (
        "import subprocess,sys,time; from pathlib import Path;"
        f" subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        " stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f" ready=Path({str(ready)!r});\n"
        "while not ready.exists(): time.sleep(0.01)\n"
    )
    runner = GitRunner(GitExecutable(sys.executable))
    assert runner.run(["-c", parent_code], timeout_seconds=5).exit_code == 0
    time.sleep(2.1)
    assert not late_write.exists()
