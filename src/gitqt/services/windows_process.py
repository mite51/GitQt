"""Own Windows command descendants before any user code starts executing.

The non-inheritable job uses KILL_ON_JOB_CLOSE, without either breakaway flag.
Existing agents are outside this job. Helpers newly spawned by this command,
including detached helpers, have the command's lifetime.

Microsoft API contracts:
https://learn.microsoft.com/windows/win32/procthread/job-objects
https://learn.microsoft.com/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread
"""

from __future__ import annotations

import ctypes
import subprocess
import time
from contextlib import contextmanager

# Fixed-width Windows types also make structure-layout tests valid on POSIX.
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", SIZE_T),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", ctypes.c_uint64 * 6),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", DWORD),
        ("TotalProcesses", DWORD),
        ("ActiveProcesses", DWORD),
        ("TotalTerminatedProcesses", DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("cntUsage", DWORD),
        ("th32ThreadID", DWORD),
        ("th32OwnerProcessID", DWORD),
        ("tpBasePri", LONG),
        ("tpDeltaPri", LONG),
        ("dwFlags", DWORD),
    ]


class WindowsAPI:
    """Small documented kernel32 boundary; instantiated only on Windows."""

    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([HANDLE, ctypes.c_wchar_p], HANDLE),
            "SetInformationJobObject": ([HANDLE, DWORD, HANDLE, DWORD], LONG),
            "QueryInformationJobObject": ([HANDLE, DWORD, HANDLE, DWORD, HANDLE], LONG),
            "AssignProcessToJobObject": ([HANDLE, HANDLE], LONG),
            "TerminateJobObject": ([HANDLE, DWORD], LONG),
            "OpenProcess": ([DWORD, LONG, DWORD], HANDLE),
            "CreateToolhelp32Snapshot": ([DWORD, DWORD], HANDLE),
            "Thread32First": ([HANDLE, ctypes.POINTER(_ThreadEntry)], LONG),
            "Thread32Next": ([HANDLE, ctypes.POINTER(_ThreadEntry)], LONG),
            "OpenThread": ([DWORD, LONG, DWORD], HANDLE),
            "ResumeThread": ([HANDLE], DWORD),
            "CloseHandle": ([HANDLE], LONG),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes = arguments
            function.restype = result

    @staticmethod
    def _check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def create_job(self):
        # NULL SECURITY_ATTRIBUTES gives a non-inheritable, unnamed handle.
        return self._check(self.kernel.CreateJobObjectW(None, None))

    def configure(self, job):
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self._check(
            self.kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        )

    def assign(self, job, pid):
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE; do not inherit this handle either.
        process = self._check(self.kernel.OpenProcess(0x0100 | 0x0001, False, pid))
        try:
            self._check(self.kernel.AssignProcessToJobObject(job, process))
        finally:
            self.close_handle(process)

    def resume(self, pid):
        # Popen closes CreateProcess's initial thread handle. The process is still
        # suspended, so recover its sole thread using documented Toolhelp APIs.
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            thread_ids = []
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread_ids.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(ctypes.get_last_error())
            if len(thread_ids) != 1:
                raise OSError("Unable to identify the suspended command's initial thread.")
            thread = self._check(self.kernel.OpenThread(0x0002, False, thread_ids[0]))
            try:
                count = self.kernel.ResumeThread(thread)
                if count == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if count != 1:
                    raise OSError("The command's initial thread was not safely suspended.")
            finally:
                self.close_handle(thread)
        finally:
            self.close_handle(snapshot)

    def terminate(self, job):
        self._check(self.kernel.TerminateJobObject(job, 1))

    def active_processes(self, job):
        accounting = _Accounting()
        self._check(
            self.kernel.QueryInformationJobObject(
                job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
            )
        )
        return accounting.ActiveProcesses

    def close_handle(self, handle):
        self._check(self.kernel.CloseHandle(handle))


class WindowsJob:
    def __init__(self, api=None):
        self.api = api or WindowsAPI()
        self.handle = self.api.create_job()
        try:
            self.api.configure(self.handle)
        except BaseException:
            self.api.close_handle(self.handle)
            self.handle = None
            raise

    def close(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            self.api.terminate(handle)
            deadline = time.monotonic() + 10
            while self.api.active_processes(handle):
                if time.monotonic() >= deadline:
                    raise OSError(
                        "Windows did not confirm command-tree termination within 10 seconds."
                    )
                time.sleep(0.01)
        finally:
            # Also enforces termination if query/TerminateJobObject failed.
            self.api.close_handle(handle)


@contextmanager
def owned_windows_process(command, *, api=None, popen=None, **options):
    """Fail closed on launch/setup errors and always dispose command descendants."""
    job = WindowsJob(api)
    process = None
    try:
        process = (popen or subprocess.Popen)(
            command,
            creationflags=CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP,
            **options,
        )
        job.api.assign(job.handle, process.pid)
        job.api.resume(process.pid)
        yield process, job
    finally:
        try:
            job.close()
        finally:
            if process is not None:
                # Assignment may have failed while the parent was still suspended.
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
