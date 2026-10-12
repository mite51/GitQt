"""Build a local desktop distribution; never publish automatically."""

import gzip
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_date_epoch() -> int:
    """Return the timestamp used for reproducible portable archives."""
    value = os.environ.get("SOURCE_DATE_EPOCH", "315532800")
    try:
        epoch = int(value)
    except ValueError as error:
        raise ValueError("SOURCE_DATE_EPOCH must be an integer Unix timestamp") from error
    if epoch < 0:
        raise ValueError("SOURCE_DATE_EPOCH must not be negative")
    return epoch


def _normalized_tar_info(info: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    info.mtime = epoch
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def _write_tar_archive(source: Path, archive: Path, epoch: int) -> None:
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as output:
                output.add(
                    source,
                    arcname=source.name,
                    recursive=True,
                    filter=lambda info: _normalized_tar_info(info, epoch),
                )


def _write_zip_archive(source: Path, archive: Path, epoch: int) -> None:
    # ZIP cannot represent dates before 1980.
    timestamp = time.gmtime(max(epoch, 315532800))[:6]
    with zipfile.ZipFile(
        archive, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as output:
        for path in [source, *sorted(source.rglob("*"))]:
            relative = Path(source.name) / path.relative_to(source)
            name = relative.as_posix() + ("/" if path.is_dir() else "")
            info = zipfile.ZipInfo(name, timestamp)
            info.create_system = 3
            info.external_attr = (path.lstat().st_mode & 0xFFFF) << 16
            if path.is_dir():
                output.writestr(info, b"")
            elif path.is_symlink():
                output.writestr(info, os.readlink(path).encode())
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as stream:
                    output.writestr(info, stream.read())


def write_portable_archive(source: Path, archive: Path, epoch: int) -> None:
    """Archive a frozen directory with stable order, ownership, and timestamps."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    if archive.name.endswith(".tar.gz"):
        _write_tar_archive(source, archive, epoch)
    elif archive.suffix == ".zip":
        _write_zip_archive(source, archive, epoch)
    else:
        raise ValueError(f"Unsupported portable archive format: {archive.name}")


def prepare():
    target = ROOT / "build/dependency-notices"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    inventory = {}
    for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pyinstaller"):
        dist = metadata.distribution(name)
        inventory[name] = dist.version
        for file in dist.files or []:
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = target / name / str(file)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    (target / "versions.json").write_text(json.dumps(inventory, indent=2) + "\n")
    import sysconfig

    license_path = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if license_path.exists():
        shutil.copy2(license_path, target / "PYTHON-LICENSE.txt")
    # Qt renders the repository-owned vector icon; Pillow only encodes platform icon containers.
    from PIL import Image
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    png = ROOT / "build/gitqt.png"
    QIcon(str(ROOT / "src/gitqt/ui/icons/gitqt.svg")).pixmap(256, 256).save(str(png))
    with Image.open(png) as icon:
        icon.save(ROOT / "build/gitqt.ico")
        if sys.platform == "darwin":
            icon.resize((1024, 1024)).save(ROOT / "build/gitqt.icns")
    return app


def main():
    os.chdir(ROOT)
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(ROOT / "build/pyinstaller-cache"))
    version = metadata.version("gitqt")
    os.environ["GITQT_VERSION"] = version
    qt_app = prepare()
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "packaging/gitqt.spec",
            ],
            check=True,
        )
        stem = f"GitQt-{version}-{platform.system().lower()}-{platform.machine()}"
        release = ROOT / "dist/releases"
        release.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            archive = release / f"{stem}.zip"
            archive.unlink(missing_ok=True)
            subprocess.run(
                [
                    "ditto",
                    "-c",
                    "-k",
                    "--sequesterRsrc",
                    "--keepParent",
                    "dist/GitQt.app",
                    str(archive),
                ],
                check=True,
            )
        else:
            suffix = ".zip" if os.name == "nt" else ".tar.gz"
            archive = release / f"{stem}{suffix}"
            write_portable_archive(ROOT / "dist/GitQt", archive, source_date_epoch())
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        archive.with_name(archive.name + ".sha256").write_text(
            f"{digest}  {archive.name}\n", encoding="utf-8"
        )
        print(archive)
    finally:
        qt_app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
