"""Create native installers from an already built application bundle."""

import importlib.metadata as metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project_version() -> str:
    from gitqt import __version__

    installed_version = metadata.version("gitqt")
    if installed_version != __version__:
        raise RuntimeError(
            "Package metadata and gitqt.__version__ disagree: "
            f"{installed_version!r} != {__version__!r}"
        )
    return installed_version


def linux_control(version: str, arch: str, libc_version: str, installed_size: int) -> str:
    if not libc_version:
        raise RuntimeError("Could not determine the build host's glibc version")
    return (
        f"Package: gitqt\nVersion: {version}\nArchitecture: {arch}\n"
        "Maintainer: GitQt contributors\nSection: devel\nPriority: optional\n"
        f"Installed-Size: {installed_size}\n"
        f"Depends: git (>= 1:2.30), libc6 (>= {libc_version}), libxcb-cursor0, "
        "libxkbcommon-x11-0, libegl1, libgl1\n"
        "Description: GitQt desktop Git client\n"
        " A local-first Git client with a Qt interface.\n"
    )


def _require_bundle(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"Frozen application bundle is missing: {path}. Run scripts/build_release.py first."
        )


def main():
    version = project_version()
    output = ROOT / "dist/releases"
    output.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("linux"):
        bundle = ROOT / "dist/GitQt"
        _require_bundle(bundle)
        stage = ROOT / "build/deb"
        if stage.exists():
            shutil.rmtree(stage)
        shutil.copytree(bundle, stage / "opt/gitqt", symlinks=True)
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
        installed_size = (
            sum(item.stat().st_size for item in stage.rglob("*") if item.is_file()) // 1024
        )
        (stage / "DEBIAN/control").write_text(
            linux_control(version, arch, platform.libc_ver()[1], installed_size),
            encoding="utf-8",
        )
        for item in stage.rglob("*"):
            if item.is_file() and not item.is_symlink():
                item.chmod(0o755 if item.stat().st_mode & 0o111 else 0o644)
        artifact = output / f"GitQt-{version}-linux-{arch}.deb"
        artifact.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment.setdefault("SOURCE_DATE_EPOCH", "315532800")
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(stage), str(artifact)],
            check=True,
            env=environment,
        )
    elif sys.platform == "win32":
        _require_bundle(ROOT / "dist/GitQt")
        compiler = shutil.which("ISCC") or str(
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Inno Setup 6/ISCC.exe"
        )
        if not Path(compiler).is_file():
            raise FileNotFoundError("Inno Setup 6 compiler (ISCC.exe) was not found")
        artifact = output / f"GitQt-{version}-windows-x86_64-setup.exe"
        artifact.unlink(missing_ok=True)
        subprocess.run(
            [compiler, f"/DAppVersion={version}", str(ROOT / "packaging/windows.iss")],
            check=True,
        )
        if not artifact.is_file():
            raise FileNotFoundError(f"Inno Setup did not produce the expected artifact: {artifact}")
    elif sys.platform == "darwin":
        bundle = ROOT / "dist/GitQt.app"
        _require_bundle(bundle)
        stage = ROOT / "build/dmg"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        shutil.copytree(bundle, stage / "GitQt.app", symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        artifact = output / f"GitQt-{version}-macos-{platform.machine()}.dmg"
        artifact.unlink(missing_ok=True)
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
    else:
        raise RuntimeError(f"Unsupported installer platform: {sys.platform}")
    print(artifact)


if __name__ == "__main__":
    main()
