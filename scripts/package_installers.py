"""Create native installers from an already built application bundle."""

import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    from gitqt import __version__

    output = ROOT / "dist/releases"
    output.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("linux"):
        stage = ROOT / "build/deb"
        if stage.exists():
            shutil.rmtree(stage)
        shutil.copytree(ROOT / "dist/GitQt", stage / "opt/gitqt")
        (stage / "usr/bin").mkdir(parents=True)
        (stage / "usr/bin/gitqt").symlink_to("/opt/gitqt/GitQt")
        for source, destination in [
            (
                "packaging/org.gitqt.desktop.desktop",
                "usr/share/applications/org.gitqt.desktop.desktop",
            ),
            (
                "src/gitqt/ui/icons/gitqt.svg",
                "usr/share/icons/hicolor/scalable/apps/org.gitqt.desktop.svg",
            ),
        ]:
            path = stage / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / source, path)
        (stage / "DEBIAN").mkdir()
        for directory in [stage, *[item for item in stage.rglob("*") if item.is_dir()]]:
            directory.chmod(0o755)
        arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
        (stage / "DEBIAN/control").write_text(
            f"Package: gitqt\nVersion: {__version__}\nArchitecture: {arch}\nMaintainer: GitQt"
            " contributors\nSection: devel\nPriority: optional\nDepends: git (>= 1:2.30), libc6"
            f" (>= {platform.libc_ver()[1]}), libxcb-cursor0, libxkbcommon-x11-0, libegl1, libgl1\n"
            "Description: GitQt"
            " desktop Git client\n A local-first Git client with a Qt interface.\n"
        )
        for item in stage.rglob("*"):
            if item.is_file() and not item.is_symlink():
                item.chmod(0o755 if item.stat().st_mode & 0o111 else 0o644)
        artifact = output / f"GitQt-{__version__}-linux-{arch}.deb"
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(stage), str(artifact)], check=True
        )
    elif sys.platform == "win32":
        compiler = shutil.which("ISCC") or str(
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Inno Setup 6/ISCC.exe"
        )
        subprocess.run(
            [compiler, f"/DAppVersion={__version__}", str(ROOT / "packaging/windows.iss")],
            check=True,
        )
    else:
        stage = ROOT / "build/dmg"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        shutil.copytree(ROOT / "dist/GitQt.app", stage / "GitQt.app", symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        artifact = output / f"GitQt-{__version__}-macos-{platform.machine()}.dmg"
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "GitQt",
                "-srcfolder",
                str(stage),
                "-ov",
                "-format",
                "UDZO",
                str(artifact),
            ],
            check=True,
        )
    for path in output.iterdir():
        if path.suffix in {".deb", ".exe", ".dmg"}:
            digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
            path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")


if __name__ == "__main__":
    main()
