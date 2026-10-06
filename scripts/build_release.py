"""Build a local desktop distribution; never publish automatically."""

import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    target = ROOT / "build/dependency-notices"
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
    qt_app = prepare()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/gitqt.spec"],
        check=True,
    )
    version = metadata.version("gitqt")
    stem = f"GitQt-{version}-{platform.system().lower()}-{platform.machine()}"
    release = ROOT / "dist/releases"
    release.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        archive = release / f"{stem}.zip"
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
        archive = Path(
            shutil.make_archive(
                str(release / stem), "zip" if os.name == "nt" else "gztar", ROOT / "dist", "GitQt"
            )
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    print(archive)
    qt_app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
