"""Bridge Git/OpenSSH askpass to Qt, preserving existing helpers and agents."""

from __future__ import annotations

import json
import os
import secrets
import shlex
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

from gitqt.services.process_context import current_context, redact


class CredentialEnvironment(dict):
    """One command's bridge capability; revoke it as soon as the command ends."""

    def __init__(self, values, close):
        super().__init__(values)
        self.close = close


class CredentialRequest:
    def __init__(self, prompt, context, revoked=None):
        self.prompt = prompt
        self.context = context
        self.done = threading.Event()
        self.answer = None
        self.expires = time.monotonic() + 600
        self.revoked = revoked or threading.Event()

    def expired(self):
        return (
            self.revoked.is_set()
            or time.monotonic() > self.expires
            or (
                self.context is not None
                and (self.context.cancel.is_set() or self.context.finished.is_set())
            )
        )


class CredentialBroker(QObject):
    requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.requested.connect(self._prompt)
        self._tokens = {}
        self._lock = threading.Lock()
        self._closed = False
        self._temporary = tempfile.TemporaryDirectory(prefix="gitqt-askpass-")
        client = Path(__file__).with_name("askpass_client.py")
        self._helper = Path(self._temporary.name) / (
            "askpass.cmd" if os.name == "nt" else "askpass"
        )
        if getattr(sys, "frozen", False):
            self._helper = Path(sys.executable).with_name(
                "GitQt-askpass.exe" if os.name == "nt" else "GitQt-askpass"
            )
            if not self._helper.is_file():
                raise OSError("The bundled authentication helper is missing. Reinstall GitQt.")
        else:
            if os.name == "nt":
                script = f'@echo off\r\n"{sys.executable}" "{client}" %*\r\n'
            else:
                script = (
                    f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(client))}"
                    ' "$@"\n'
                )
            self._helper.write_text(script)
            self._helper.chmod(0o700)
        broker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(610)
                try:
                    payload = json.loads(self.rfile.readline(8192))
                    if not isinstance(payload, dict) or not isinstance(payload.get("token"), str):
                        return
                    with broker._lock:
                        if payload.get("token") not in broker._tokens:
                            return
                        context, expiry, revoked = broker._tokens[payload["token"]]
                        if expiry <= time.monotonic():
                            return
                    request = CredentialRequest(
                        str(payload.get("prompt", "Credential")), context, revoked
                    )
                    if request.expired():
                        return
                    broker.requested.emit(request)
                    while not request.done.wait(0.1):
                        if request.expired():
                            break
                    self.wfile.write(
                        (
                            json.dumps(request.answer if not request.expired() else None) + "\n"
                        ).encode()
                    )
                    request.answer = None
                except (OSError, ValueError, KeyError):
                    return

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        QApplication.instance().aboutToQuit.connect(self.close)

    def environment(self):
        token = secrets.token_urlsafe(32)
        revoked = threading.Event()
        with self._lock:
            if self._closed:
                raise OSError("The authentication service is closed. Restart GitQt.")
            self._tokens = {
                key: value
                for key, value in self._tokens.items()
                if value[1] > time.monotonic() and not (value[0] and value[0].finished.is_set())
            }
            self._tokens[token] = (current_context(), time.monotonic() + 610, revoked)

        def release():
            revoked.set()
            with self._lock:
                self._tokens.pop(token, None)

        return CredentialEnvironment(
            {
                "GIT_ASKPASS": str(self._helper),
                "SSH_ASKPASS": str(self._helper),
                "SSH_ASKPASS_REQUIRE": "force",
                "GIT_TERMINAL_PROMPT": "0",
                "GITQT_ASKPASS_PORT": str(self._server.server_address[1]),
                "GITQT_ASKPASS_TOKEN": token,
            },
            release,
        )

    @Slot(object)
    def _prompt(self, request):
        if request.expired():
            request.done.set()
            return
        dialog = QInputDialog(QApplication.activeWindow())
        dialog.setWindowTitle("Git Authentication")
        dialog.setLabelText(redact(request.prompt, request.context))
        prompt = request.prompt.lstrip().lower()
        # A username or key filename can itself contain "username" or "fingerprint".
        # Only known public prompt forms should disable password masking.
        public_input = prompt.startswith("username") or (
            prompt.startswith("are you sure") and "yes/no" in prompt
        )
        dialog.setTextEchoMode(
            QLineEdit.EchoMode.Normal if public_input else QLineEdit.EchoMode.Password
        )
        timer = QTimer(dialog)
        timer.setInterval(100)
        timer.timeout.connect(lambda: dialog.reject() if request.expired() else None)
        timer.start()
        if dialog.exec():
            request.answer = dialog.textValue()
            if request.context is not None and not public_input:
                request.context.secrets.append(request.answer)
        else:
            # Cancelling a credential request cancels the operation, including retries by Git.
            if request.context is not None:
                request.context.cancel.set()
        request.done.set()
        dialog.deleteLater()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for _, _, revoked in self._tokens.values():
                revoked.set()
            self._tokens.clear()
        self._server.shutdown()
        self._server.server_close()
        self._temporary.cleanup()
