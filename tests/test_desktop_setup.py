import sys
import time
from threading import Thread

from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit
from test_visible_workflows import git

from gitqt.services.credentials import CredentialBroker
from gitqt.services.git import GitCommandCancelledError, GitCommandFailure, GitExecutable, GitRunner
from gitqt.services.process_context import ProcessContext, process_context, redact
from gitqt.services.settings import AppSettings, SettingsStore
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.settings_dialog import SettingsDialog

pytest_plugins = ["test_visible_workflows"]


def pump(app, thread):
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    thread.join(0.5)
    assert not thread.is_alive()


def test_real_git_https_askpass_exchange_and_retry(app, runner, monkeypatch, tmp_path):
    broker = CredentialBroker()
    runner.credential_environment = broker.environment
    prompts = []
    secret = "test-secret-value"

    def respond(dialog):
        prompts.append((dialog.labelText(), dialog.textEchoMode()))
        dialog.setTextValue("person" if "username" in dialog.labelText().lower() else secret)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QInputDialog, "exec", respond)
    context = ProcessContext()
    output, errors = [], []

    def work():
        with process_context(context):
            try:
                for _ in range(2):
                    output.append(
                        runner.run(
                            ["-c", "credential.helper=", "credential", "fill"],
                            cwd=tmp_path,
                            input_text="protocol=https\nhost=example.invalid\n\n",
                        ).stdout
                    )
            except Exception as error:
                errors.append(error)

    thread = Thread(target=work)
    try:
        thread.start()
        pump(app, thread)
        assert not errors
        assert len(output) == 2 and all(f"password={secret}" in result for result in output)
        assert len(prompts) == 4
        assert prompts[1][1] == QLineEdit.EchoMode.Password
        assert redact(f"helper echoed {secret}", context) == "helper echoed [redacted]"
        assert secret not in broker._helper.read_text()
        assert list(tmp_path.iterdir()) == []
    finally:
        context.cancel.set()
        broker.close()


def test_cancel_credential_prompt_ends_operation_and_allows_retry(
    app, runner, monkeypatch, tmp_path
):
    broker = CredentialBroker()
    runner.credential_environment = broker.environment
    monkeypatch.setattr(QInputDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    context = ProcessContext()
    errors = []

    def work():
        with process_context(context):
            try:
                runner.run(
                    ["-c", "credential.helper=", "credential", "fill"],
                    cwd=tmp_path,
                    input_text="protocol=https\nhost=example.invalid\n\n",
                )
            except (GitCommandFailure, GitCommandCancelledError) as error:
                errors.append(error)

    thread = Thread(target=work)
    try:
        thread.start()
        pump(app, thread)
        assert errors and context.cancel.is_set()
        assert runner.run(["--version"]).stdout.startswith("git version")
    finally:
        broker.close()


def test_passphrase_prompt_uses_ssh_askpass_bridge(app, runner, monkeypatch):
    broker = CredentialBroker()
    monkeypatch.setattr(
        QInputDialog,
        "exec",
        lambda dialog: (dialog.setTextValue("ssh-passphrase"), QDialog.DialogCode.Accepted)[1],
    )
    helper_runner = GitRunner(GitExecutable(sys.executable))
    helper_runner.credential_environment = broker.environment
    output = []
    context = ProcessContext()

    def work():
        with process_context(context):
            output.append(
                helper_runner.run(
                    [
                        "-c",
                        (
                            "import subprocess,os; raise"
                            " SystemExit(subprocess.call([os.environ['SSH_ASKPASS'], 'Enter"
                            " passphrase for key']))"
                        ),
                    ]
                ).stdout
            )

    thread = Thread(target=work)
    try:
        thread.start()
        pump(app, thread)
        assert output == ["ssh-passphrase\n"]
        assert "ssh-passphrase" in context.secrets
    finally:
        broker.close()


def test_preferences_rejects_non_git_executable_without_saving(app, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    dialog = SettingsDialog(store, AppSettings())
    dialog._jobs.pool = ImmediateWorkerPool()
    dialog._git_input.setText(sys.executable)
    dialog._on_save()
    assert not store.path.exists()
    assert "Git 2.30" in dialog._validation.text()
    assert dialog._save_button.isEnabled()
    dialog.close()


def test_preferences_validates_and_saves_git(app, tmp_path, runner):
    store = SettingsStore(tmp_path / "settings.json")
    dialog = SettingsDialog(store, AppSettings())
    dialog._jobs.pool = ImmediateWorkerPool()
    dialog._git_input.setText(runner._executable.path)
    dialog._on_save()
    assert store.load().git_executable == runner._executable.path


def test_identity_editor_scopes_repository_config(app, repo, tmp_path):
    from gitqt.app.bootstrap import build_services
    from gitqt.ui.setup_dialogs import IdentityDialog

    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    dialog = IdentityDialog(services, str(repo))
    assert dialog.name.text() == "Test User"
    dialog.name.setText("New Author")
    dialog.email.setText("new@example.invalid")
    dialog.write_identity()
    assert git(repo, "config", "--local", "user.name").stdout == b"New Author\n"
    assert git(repo, "config", "--local", "user.email").stdout == b"new@example.invalid\n"


def test_disabling_crash_reports_removes_live_hooks(tmp_path):
    import threading

    from gitqt.services.crash_reporting import CrashReporter

    previous_sys, previous_thread = sys.excepthook, threading.excepthook
    reporter = CrashReporter(tmp_path / "reports")
    try:
        reporter.set_enabled(True)
        assert sys.excepthook == reporter._sys_excepthook
        reporter.set_enabled(False)
        assert sys.excepthook == previous_sys
        assert threading.excepthook == previous_thread
    finally:
        sys.excepthook, threading.excepthook = previous_sys, previous_thread


def test_cancelling_operation_closes_pending_authentication_prompt(app, runner, tmp_path):
    from PySide6.QtCore import QTimer

    broker = CredentialBroker()
    runner.credential_environment = broker.environment
    context = ProcessContext()
    errors = []
    timer = QTimer()
    timer.setInterval(10)

    def cancel_when_prompted():
        if any(
            isinstance(widget, QInputDialog) and widget.isVisible()
            for widget in app.topLevelWidgets()
        ):
            context.cancel.set()

    timer.timeout.connect(cancel_when_prompted)
    timer.start()

    def work():
        with process_context(context):
            try:
                runner.run(
                    ["-c", "credential.helper=", "credential", "fill"],
                    cwd=tmp_path,
                    input_text="protocol=https\nhost=example.invalid\n\n",
                )
            except (GitCommandFailure, GitCommandCancelledError) as error:
                errors.append(error)

    thread = Thread(target=work)
    try:
        thread.start()
        pump(app, thread)
        assert errors and context.cancel.is_set()
        assert not any(
            isinstance(widget, QInputDialog) and widget.isVisible()
            for widget in app.topLevelWidgets()
        )
    finally:
        timer.stop()
        broker.close()
