# Build on each target OS; PyInstaller is not a cross compiler.
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
identity = os.environ.get("GITQT_CODESIGN_IDENTITY")
app_version = os.environ.get("GITQT_VERSION")
if not app_version:
    raise RuntimeError("GITQT_VERSION must be supplied by scripts/build_release.py")
data = collect_data_files("gitqt", includes=["ui/fonts/*", "ui/icons/*"])
data += [(str(root / name), ".") for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md", "README.md", "RELEASE.md")]
data += [(str(root / "licenses"), "licenses"), (str(root / "build" / "dependency-notices"), "licenses/dependencies")]
a = Analysis([str(root / "packaging/desktop_entry.py")], pathex=[str(root / "src")], datas=data,
             excludes=["tkinter", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"], noarchive=False)
main = EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name="GitQt", console=sys.platform != "win32",
           codesign_identity=identity, upx=False, icon=str(root / "build" / ("gitqt.icns" if sys.platform == "darwin" else "gitqt.ico")))
h = Analysis([str(root / "packaging/askpass_entry.py")], pathex=[str(root / "src")], excludes=["PySide6", "tkinter"], noarchive=False)
helper = EXE(PYZ(h.pure), h.scripts, [], exclude_binaries=True, name="GitQt-askpass", console=True,
             codesign_identity=identity, upx=False)
folder = COLLECT(main, helper, a.binaries, a.datas, h.binaries, h.datas, name="GitQt", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(folder, name="GitQt.app", icon=str(root / "build/gitqt.icns"),
                 bundle_identifier="org.gitqt.desktop", version=app_version,
                 info_plist={"NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication"})
