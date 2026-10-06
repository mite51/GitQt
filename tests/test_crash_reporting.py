from pathlib import Path

from gitqt.services.crash_reporting import CrashReporter


def test_crash_reporter_writes_local_report(tmp_path: Path) -> None:
    reporter = CrashReporter(tmp_path / "crash-reports")

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        report_path = reporter.write_report(type(exc), exc, exc.__traceback__)

    assert report_path.exists()
    assert report_path.parent == tmp_path / "crash-reports"
    content = report_path.read_text(encoding="utf-8")
    assert "exception_type=RuntimeError" in content
    assert "boom" in content
