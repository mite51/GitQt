import tarfile
import tomllib
from pathlib import Path

import pytest

from gitqt import __version__
from scripts.build_release import source_date_epoch, write_portable_archive
from scripts.checksum_artifacts import verify_checksums, write_checksums
from scripts.package_installers import linux_control, project_version

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_has_one_consistent_value(monkeypatch) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    spec = (ROOT / "packaging/gitqt.spec").read_text(encoding="utf-8")

    assert project["version"] == __version__ == project_version()
    assert 'os.environ.get("GITQT_VERSION")' in spec
    assert "version=app_version" in spec
    assert 'version="0.1.0"' not in spec

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-a-timestamp")
    with pytest.raises(ValueError, match="integer Unix timestamp"):
        source_date_epoch()


def test_portable_tar_archive_is_reproducible_and_normalized(tmp_path) -> None:
    source = tmp_path / "GitQt"
    (source / "nested").mkdir(parents=True)
    executable = source / "GitQt"
    executable.write_text("executable", encoding="utf-8")
    executable.chmod(0o755)
    (source / "nested/data.txt").write_text("payload", encoding="utf-8")
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    write_portable_archive(source, first, 1_700_000_000)
    for path in source.rglob("*"):
        path.touch()
    write_portable_archive(source, second, 1_700_000_000)

    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == sorted(member.name for member in members)
    assert all(member.mtime == 1_700_000_000 for member in members)
    identities = [(member.uid, member.gid, member.uname, member.gname) for member in members]
    assert all(identity == (0, 0, "root", "root") for identity in identities)
    assert next(member for member in members if member.name == "GitQt/GitQt").mode & 0o777 == 0o755


def test_checksum_gate_detects_tampering_and_orphaned_sidecars(tmp_path) -> None:
    release = tmp_path / "releases"
    release.mkdir()
    artifact = release / "GitQt-1.0-linux-amd64.deb"
    artifact.write_bytes(b"installer")

    assert write_checksums(tmp_path) == [artifact.with_name(artifact.name + ".sha256")]
    assert verify_checksums(tmp_path) == [artifact]

    artifact.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="Checksum verification failed"):
        verify_checksums(tmp_path)

    artifact.write_bytes(b"installer")
    orphan = release / "removed.zip.sha256"
    orphan.write_text("0" * 64 + "  removed.zip\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="without matching artifacts"):
        verify_checksums(tmp_path)
    orphan.unlink()
    artifact.unlink()
    with pytest.raises(RuntimeError, match="No release artifacts"):
        verify_checksums(tmp_path)


def test_linux_control_declares_runtime_and_installed_size() -> None:
    control = linux_control("1.2.3", "amd64", "2.35", 4096)

    assert "Version: 1.2.3\n" in control
    assert "Architecture: amd64\n" in control
    assert "Installed-Size: 4096\n" in control
    assert "git (>= 1:2.30)" in control
    assert "libc6 (>= 2.35)" in control
    assert control.endswith(" A local-first Git client with a Qt interface.\n")


def test_native_release_workflow_runs_installer_lifecycle_and_checksum_gates() -> None:
    workflow = (ROOT / ".github/workflows/release-build.yml").read_text(encoding="utf-8")

    for platform in ("Linux", "Windows", "macOS"):
        assert f"Verify {platform} install, reinstall, and uninstall lifecycle" in workflow
    assert "scripts/checksum_artifacts.py --verify" in workflow
    assert "SOURCE_DATE_EPOCH=" in workflow
    assert "retained-after-uninstall" in workflow
