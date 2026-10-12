from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from gitqt.services.process_context import current_context, redact
from gitqt.services.windows_process import owned_windows_process


class GitNotFoundError(RuntimeError):
    """Raised when a Git executable cannot be discovered."""


@dataclass(slots=True)
class GitExecutable:
    path: str


@dataclass(slots=True)
class GitCommandResult:
    args: tuple[str, ...]
    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int


class GitOutputLimitError(RuntimeError):
    """Output exceeded the desktop preview budget; no partial patch is actionable."""


class GitCommandLaunchError(RuntimeError):
    """Raised when a Git subprocess cannot be launched."""


class GitCommandTimeoutError(RuntimeError):
    """Raised when a Git subprocess exceeds its timeout."""


class GitCommandCancelledError(RuntimeError):
    """The running process was stopped; repository changes may already exist."""


class GitCommandFailure(RuntimeError):
    """Raised when Git exits with a non-zero status code."""

    def __init__(self, result: GitCommandResult) -> None:
        message = (
            f"Git command failed with exit code {result.exit_code}: {redact(' '.join(result.args))}"
        )
        super().__init__(
            message + ("\n" + redact(result.stderr.strip()) if result.stderr.strip() else "")
        )
        self.result = result


class GitLocator:
    """Resolve a usable Git executable path."""

    def __init__(self, which_func: Callable[[str], str | None] | None = None) -> None:
        self._which = which_func or shutil.which

    def discover(self, explicit_path: str | Path | None = None) -> GitExecutable:
        if explicit_path:
            candidate = Path(explicit_path)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return GitExecutable(path=str(candidate))
            raise GitNotFoundError(f"Configured Git executable is not usable: {candidate}")

        discovered = self._which("git")
        if discovered:
            return GitExecutable(path=discovered)

        raise GitNotFoundError("Git executable could not be found on PATH.")


class GitRunner:
    """Execute Git commands with consistent result and error handling."""

    def __init__(
        self,
        executable: GitExecutable,
        run_func: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._executable = executable
        self._run = run_func or self._execute
        self.credential_environment = None
        self._monotonic = monotonic_func or time.monotonic

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        input_text: str | None = None,
        preserve_newlines: bool = False,
    ) -> GitCommandResult:
        command = [self._executable.path, *args]
        command_args = tuple(command)
        merged_env = os.environ.copy()
        if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
            original = merged_env.pop("LD_LIBRARY_PATH_ORIG", None)
            if original is None:
                merged_env.pop("LD_LIBRARY_PATH", None)
            else:
                merged_env["LD_LIBRARY_PATH"] = original
        credentials = None
        if timeout_seconds is None:
            if any(arg in {"difftool", "mergetool"} for arg in args):
                timeout_seconds = 86400
            elif args and args[0] in {"clone", "fetch", "pull", "push", "ls-remote"}:
                timeout_seconds = 600
            else:
                timeout_seconds = 120
        context = current_context()
        if context is not None and context.cancel.is_set():
            raise GitCommandCancelledError(
                "Operation cancelled. Refresh repository state before retrying."
            )

        if self.credential_environment is not None:
            credentials = self.credential_environment()
            merged_env.update(credentials)
        if env_overrides:
            merged_env.update(env_overrides)

        started = self._monotonic()
        try:
            completed = self._run(
                command,
                capture_output=True,
                text=not preserve_newlines,
                cwd=str(cwd) if cwd is not None else None,
                env=merged_env,
                timeout=timeout_seconds,
                check=False,
                input=(
                    input_text.encode("utf-8")
                    if preserve_newlines and input_text is not None
                    else input_text
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandTimeoutError(
                f"Git command timed out: {redact(' '.join(command_args))}"
            ) from exc
        except OSError as exc:
            raise GitCommandLaunchError(
                f"Unable to launch Git command: {redact(' '.join(command_args))}"
            ) from exc
        finally:
            if credentials is not None and hasattr(credentials, "close"):
                credentials.close()

        elapsed_ms = int((self._monotonic() - started) * 1000)
        result = GitCommandResult(
            args=command_args,
            stdout=(completed.stdout.decode("utf-8") if preserve_newlines else completed.stdout),
            stderr=(
                completed.stderr.decode("utf-8", errors="replace")
                if preserve_newlines
                else completed.stderr
            ),
            exit_code=completed.returncode,
            elapsed_ms=elapsed_ms,
        )
        operation = args[0] if args and args[0].isalpha() else "command"
        logging.getLogger("gitqt.git").info(
            "Git %s finished: exit=%d elapsed_ms=%d",
            operation,
            result.exit_code,
            result.elapsed_ms,
        )
        if completed.returncode != 0:
            result.stderr = redact(result.stderr)
            raise GitCommandFailure(result)
        return result

    @staticmethod
    def _execute(command, *, cwd, env, timeout, input, text, **_kwargs):
        context = current_context()
        report_output = any(
            arg
            in {
                "clone",
                "fetch",
                "pull",
                "push",
                "commit",
                "merge",
                "rebase",
                "checkout",
                "cherry-pick",
                "revert",
                "ls-remote",
            }
            for arg in command[1:]
        )
        options = {
            "cwd": cwd,
            "env": env,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            with owned_windows_process(command, **options) as (process, job):
                return GitRunner._capture(
                    process, command, timeout, input, text, context, report_output, job
                )
        process = subprocess.Popen(command, start_new_session=True, **options)
        return GitRunner._capture(process, command, timeout, input, text, context, report_output)

    @staticmethod
    def _capture(process, command, timeout, input, text, context, report_output, job=None):
        output, errors = bytearray(), bytearray()
        overflow = Event()
        output_limit = (
            2_000_000
            if any(arg in {"diff", "show", "--patch"} for arg in command[1:])
            else 64_000_000
        )

        def read(stream, destination, report=False):
            pending = b""
            while chunk := stream.read1(4096):
                if len(destination) + len(chunk) > output_limit:
                    overflow.set()
                    continue
                destination.extend(chunk)
                if report and context is not None and context.progress is not None:
                    pending += chunk
                    parts = pending.replace(b"\r", b"\n").split(b"\n")
                    pending = parts.pop()
                    for part in parts:
                        if part:
                            context.progress(
                                redact(part.decode("utf-8", errors="replace"), context)
                            )
            if pending and report and context is not None and context.progress is not None:
                context.progress(redact(pending.decode("utf-8", errors="replace"), context))
            stream.close()

        def feed():
            try:
                if input is not None:
                    process.stdin.write(input.encode("utf-8") if isinstance(input, str) else input)
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        threads = [
            Thread(
                target=read,
                args=(
                    process.stdout,
                    output,
                    report_output
                    and not any(arg in {"ls-remote", "fetch", "clone"} for arg in command[1:]),
                ),
                daemon=True,
            ),
            Thread(target=read, args=(process.stderr, errors, report_output), daemon=True),
            Thread(target=feed, daemon=True),
        ]
        for thread in threads:
            thread.start()
        started = time.monotonic()
        failure = None
        # Descendants (hooks, SSH, credential helpers) can outlive Git while retaining
        # its pipes. Continue enforcing cancellation and timeout until all I/O ends.
        while process.poll() is None or any(thread.is_alive() for thread in threads):
            if overflow.is_set():
                failure = GitOutputLimitError(
                    "This result exceeds the desktop preview limit. Narrow the selection or use an"
                    " external viewer; whole-file staging remains available."
                )
                break
            if context is not None and context.cancel.is_set():
                failure = GitCommandCancelledError(
                    "Operation cancelled. Some changes may have completed; repository state will"
                    " refresh."
                )
                break
            if time.monotonic() - started > timeout:
                failure = subprocess.TimeoutExpired(command, timeout)
                break
            time.sleep(0.025)
        if failure is not None:
            if job is not None:
                job.close()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=1)
                except (subprocess.TimeoutExpired, ProcessLookupError):
                    pass
                # A child may ignore SIGTERM even after its parent has exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait()
        for thread in threads:
            thread.join(timeout=2)
        if failure is not None:
            raise failure
        if overflow.is_set():
            raise GitOutputLimitError(
                "This result exceeds the desktop preview limit. Use an external viewer; whole-file"
                " staging remains available."
            )
        stdout, stderr = bytes(output), bytes(errors)
        if text:
            stdout = stdout.decode("utf-8").replace("\r\n", "\n")
            stderr = stderr.decode("utf-8", errors="replace").replace("\r\n", "\n")
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
