from PySide6.QtCore import QCoreApplication, QEvent, Qt
from shiboken6 import isValid

from gitqt.app.bootstrap import build_services
from gitqt.services.worker_pool import ImmediateWorkerPool
from gitqt.ui.repository_view import RepositoryView


def test_deleted_repository_view_disconnects_application_activation(qtbot, tmp_path):
    services = build_services(base_config_dir=tmp_path / "settings")
    services.worker_pool = ImmediateWorkerPool()
    view = RepositoryView(services)
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(view)
    # Qt disconnects bound QObject slots on destruction; a lambda capturing the
    # deleted wrapper instead raised a runtime error on every later activation.
    QCoreApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
