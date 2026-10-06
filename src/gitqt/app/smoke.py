"""Acceptance check run by the built executable outside the source tree."""

import json
import tempfile
import threading
import time
from pathlib import Path


def smoke_test():
    from PySide6.QtGui import QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication

    from gitqt import __version__
    from gitqt.app.bootstrap import build_services
    from gitqt.services.process_context import ProcessContext, process_context
    from gitqt.ui.main_window import MainWindow
    from gitqt.ui.theme import apply_dark_palette

    app = QApplication.instance() or QApplication([])
    apply_dark_palette(app)
    with tempfile.TemporaryDirectory(
        prefix="gitqt-smoke-", ignore_cleanup_errors=True
    ) as temporary:
        root = Path(temporary)
        services = build_services(base_config_dir=root / "settings")
        if services.git_runner is None:
            raise RuntimeError("Smoke test requires Git 2.30+ on PATH.")
        runner = services.git_runner
        repository = root / "repository"
        runner.run(["init", "--initial-branch=main", str(repository)])
        runner.run(["config", "user.name", "GitQt Smoke"], cwd=repository)
        runner.run(["config", "user.email", "smoke@example.invalid"], cwd=repository)
        runner.run(["config", "commit.gpgsign", "false"], cwd=repository)
        (repository / "README.md").write_text("# Smoke\n")
        runner.run(["add", "README.md"], cwd=repository)
        runner.run(["commit", "-m", "Installed application smoke test"], cwd=repository)
        window = MainWindow(services)
        window.show()
        window._activate_repository(str(repository))
        broker = services.credential_broker
        if broker is None:
            raise RuntimeError("Desktop authentication bridge did not start.")
        broker.requested.disconnect()

        def answer(request):
            request.answer = "gitqt-smoke-secret"
            request.done.set()

        broker.requested.connect(answer)
        result, errors = [], []

        def credential_check():
            try:
                with process_context(ProcessContext()):
                    result.append(
                        runner.run(
                            ["-c", "credential.helper=", "credential", "fill"],
                            cwd=repository,
                            input_text="protocol=https\nhost=example.invalid\n\n",
                        ).stdout
                    )
            except Exception as error:
                errors.append(str(error))

        thread = threading.Thread(target=credential_check)
        thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            if not thread.is_alive() and services.worker_pool.wait_for_done(1):
                app.processEvents()
                break
            time.sleep(0.01)
        thread.join(1)
        try:
            assert not thread.is_alive(), "Authentication helper did not finish"
            assert not errors, errors
            assert "password=gitqt-smoke-secret" in result[0]
            assert window._repository_view._working_tree_vm.state.repository_path == str(repository)
            assert "Inter" in QFontDatabase.families(), "Bundled font missing"
            icon = Path(__file__).parents[1] / "ui/icons/gitqt.svg"
            assert not QIcon(str(icon)).pixmap(32, 32).isNull(), "SVG Qt plugin or icon missing"
            assert not window.grab().isNull(), "Qt window rendering failed"
            print(
                json.dumps(
                    {
                        "app": "GitQt",
                        "version": __version__,
                        "git": True,
                        "qt": True,
                        "fonts": True,
                        "icons": True,
                        "credential_helper": True,
                    }
                )
            )
        finally:
            window.close()
            broker.close()
    return 0
