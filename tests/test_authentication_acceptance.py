"""Local authentication and lifecycle acceptance; no hosting-provider credentials."""

import base64
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit
from test_desktop_setup import pump

from gitqt.services.credentials import CredentialBroker, CredentialRequest
from gitqt.services.external_tools import ExternalToolService
from gitqt.services.git import (
    GitCommandFailure,
    GitCommandTimeoutError,
    GitExecutable,
    GitLocator,
    GitNotFoundError,
    GitRunner,
)
from gitqt.services.process_context import ProcessContext, process_context, redact

pytest_plugins = ["test_visible_workflows"]


@pytest.mark.parametrize("scheme", ["Bearer", "Basic"])
def test_authorization_header_redacts_credentials(scheme):
    assert redact(f"Authorization: {scheme} secret-value\nnext line") == (
        "Authorization: [redacted]\nnext line"
    )
    assert "secret-value" not in redact(f"Proxy-Authorization: {scheme} secret-value")


def test_prompt_redacts_secrets_from_worker_context(app, monkeypatch):
    broker = CredentialBroker()
    request = CredentialRequest("Password after prior-secret was rejected", ProcessContext())
    request.context.secrets.append("prior-secret")
    labels = []
    monkeypatch.setattr(QInputDialog, "exec", lambda dialog: labels.append(dialog.labelText()) or 0)
    try:
        broker._prompt(request)
        assert labels == ["Password after [redacted] was rejected"]
    finally:
        broker.close()


def test_git_locator_rejects_executable_directory(tmp_path):
    with pytest.raises(GitNotFoundError):
        GitLocator().discover(tmp_path)


@pytest.mark.parametrize(
    "prompt",
    ["Password for 'https://username@example.invalid':", "Enter passphrase for key fingerprint:"],
)
def test_secret_prompt_stays_masked_when_path_contains_public_prompt_words(
    app, monkeypatch, prompt
):
    broker = CredentialBroker()
    request = CredentialRequest(prompt, ProcessContext())
    modes = []
    monkeypatch.setattr(
        QInputDialog, "exec", lambda dialog: modes.append(dialog.textEchoMode()) or 0
    )
    try:
        broker._prompt(request)
        assert modes == [QLineEdit.EchoMode.Password]
    finally:
        broker.close()


def test_command_releases_askpass_capability_after_success_and_timeout(app):
    broker = CredentialBroker()
    runner = GitRunner(GitExecutable(sys.executable))
    runner.credential_environment = broker.environment
    try:
        runner.run(["-c", "print('done')"])
        assert not broker._tokens
        with pytest.raises(GitCommandTimeoutError):
            runner.run(["-c", "import time; time.sleep(30)"], timeout_seconds=0.05)
        assert not broker._tokens
        environment = broker.environment()
        revoked = broker._tokens[environment["GITQT_ASKPASS_TOKEN"]][2]
        broker.close()
        assert revoked.is_set()
        assert not broker._tokens
    finally:
        broker.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group acceptance")
def test_timeout_kills_pipe_holding_child_after_parent_exits(tmp_path):
    marker = tmp_path / "child-finished"
    child = (
        "import time; from pathlib import Path; time.sleep(1.2);"
        f" Path({str(marker)!r}).write_text('unexpected'); time.sleep(30)"
    )
    code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    runner = GitRunner(GitExecutable(sys.executable))
    started = time.monotonic()
    with pytest.raises(GitCommandTimeoutError):
        runner.run(["-c", code], timeout_seconds=0.1)
    assert time.monotonic() - started < 2
    time.sleep(1.3)
    assert not marker.exists()
    assert runner.run(["-c", "print('retry')"]).stdout == "retry\n"


def test_external_tool_expands_home_path(app, repo, tmp_path, monkeypatch, runner):
    if os.name == "nt":
        pytest.skip("POSIX shell executable fixture")
    home = tmp_path / "home with spaces"
    home.mkdir()
    marker = tmp_path / "tool-ran"
    tool = home / "diff tool"
    tool.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    (repo / "file.txt").write_text("changed\n")
    result = ExternalToolService(runner).launch_diff_tool(
        repo, tool_path="~/diff tool", file_path="file.txt"
    )
    assert result.success
    assert marker.read_text() == "ran"


def test_real_http_authentication_rejected_credentials_then_retry(app, monkeypatch, runner):
    expected = "Basic " + base64.b64encode(b"person:valid-local-token").decode()
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            authenticated = self.headers.get("Authorization") == expected
            attempts.append(authenticated)
            if not authenticated:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="GitQt local test"')
            elif self.path.startswith("/repo/info/refs"):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            else:
                self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    broker = CredentialBroker()
    runner.credential_environment = broker.environment
    secret = "expired-local-token"
    prompts = []

    def respond(dialog):
        prompts.append(dialog.labelText())
        dialog.setTextValue("person" if "username" in dialog.labelText().lower() else secret)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QInputDialog, "exec", respond)
    outcomes = []

    def work():
        with process_context(ProcessContext()):
            try:
                outcomes.append(
                    runner.run(
                        [
                            "-c",
                            "credential.helper=",
                            "ls-remote",
                            "--",
                            f"http://127.0.0.1:{server.server_port}/repo",
                        ],
                        timeout_seconds=5,
                    )
                )
            except GitCommandFailure as error:
                outcomes.append(error)

    try:
        thread = threading.Thread(target=work)
        thread.start()
        pump(app, thread)
        assert isinstance(outcomes[0], GitCommandFailure)
        assert secret not in str(outcomes[0])
        secret = "valid-local-token"
        thread = threading.Thread(target=work)
        thread.start()
        pump(app, thread)
        assert not isinstance(outcomes[1], Exception)
        assert any(attempts) and not all(attempts)
        assert len(prompts) == 4
        assert not broker._tokens
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(2)


@pytest.mark.skipif(
    os.name == "nt"
    or not all(shutil.which(tool) for tool in ("ssh-agent", "ssh-add", "ssh-keygen")),
    reason="Requires local OpenSSH agent and key tools",
)
def test_locked_ssh_key_unlocks_through_desktop_askpass(app, monkeypatch, tmp_path):
    key = tmp_path / "local test key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "local-passphrase", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    agent = subprocess.run(["ssh-agent", "-s"], check=True, capture_output=True, text=True)
    for line in agent.stdout.splitlines():
        if line.startswith(("SSH_AUTH_SOCK=", "SSH_AGENT_PID=")):
            name, value = line.split(";", 1)[0].split("=", 1)
            monkeypatch.setenv(name, value)
    broker = CredentialBroker()
    runner = GitRunner(GitExecutable(shutil.which("ssh-add")))
    runner.credential_environment = broker.environment
    prompts, outcomes = [], []

    def respond(dialog):
        prompts.append(dialog.labelText())
        dialog.setTextValue("local-passphrase")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QInputDialog, "exec", respond)

    def work():
        with process_context(ProcessContext()):
            try:
                outcomes.append(runner.run([str(key)], timeout_seconds=5))
            except Exception as error:
                outcomes.append(error)

    try:
        thread = threading.Thread(target=work)
        thread.start()
        pump(app, thread)
        assert outcomes and not isinstance(outcomes[0], Exception), outcomes
        assert len(prompts) == 1 and "passphrase" in prompts[0].lower()
        assert "ED25519" in runner.run(["-l"]).stdout
    finally:
        broker.close()
        subprocess.run(["ssh-agent", "-k"], capture_output=True, check=False)
