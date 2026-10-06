# Building and accepting a GitQt release

The Linux bundle and Debian package have been built and exercised locally. Windows and macOS have native build recipes and CI jobs; they are **not yet accepted releases**. The workflow uploads reviewable artifacts and never publishes them automatically. Signing identities, provider credentials, and release-upload access are not required for local development builds.

## Build

Use a clean Python 3.12 virtual environment on each target OS. Avoid Conda for release builds: its shared libraries can conflict with system libraries during bundling. Install Git 2.30+ separately. On Debian/Ubuntu install `libegl1`, `libgl1`, `libxcb-cursor0`, and `libxkbcommon-x11-0`; on Windows install Inno Setup 6 for the installer step.

```sh
python -m pip install '.[dev,release]'
python -m pytest -q
python scripts/build_release.py
python scripts/package_installers.py
python -m build
python scripts/checksum_artifacts.py
```

For Linux headless builds/tests, set `QT_QPA_PLATFORM=offscreen`. For normal use, leave it unset. Artifacts are written to `dist/releases/`; source archives and wheels are written to `dist/`. Keep the entire portable application directory together. The bundle contains Python, Qt, plugins, fonts, icons, and a separate credential helper. Git and SSH remain system tools. The frozen launcher resets inherited loader paths before calling external Git. See [PyInstaller's external-program guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application).

Linux produces a portable tar archive and a `.deb` with a desktop launcher. The package records the build host's glibc minimum; use an older supported build host to target older distributions. The CI recipe uses Ubuntu 22.04. The locally produced package uses glibc 2.39 and targets compatible Ubuntu 24.04 systems. Windows produces a portable ZIP and per-user Inno Setup installer with uninstall support. macOS produces an application ZIP and a drag-to-Applications DMG. Neither Windows nor macOS is cross-compiled from Linux.

## Installation, updates, and removal

Verify each download against its accompanying SHA-256 file from a trusted release source. A checksum detects changed bytes; release signatures establish publisher identity. Linux: install the `.deb` with the system package manager, or extract the portable archive and run `GitQt/GitQt`. Windows: run the setup executable. macOS: copy GitQt from the DMG into Applications.

Install a newer version over the old application. Portable users should replace the complete application folder. Automatic update download is not implemented. Uninstall using the platform package manager / installed-app controls, or remove the portable application folder; macOS users remove the application bundle.

Settings, logs, and local crash reports live separately under `~/.config/GitQt` (or `$XDG_CONFIG_HOME/GitQt`), `%APPDATA%/GitQt`, or `~/Library/Application Support/GitQt`. Uninstall leaves those files and repositories intact. Before downgrading, back up settings; migration to an older settings format is not guaranteed. Recovery references and file backups remain inside each repository's Git metadata and survive application removal.

## Required release acceptance

- Run the native CI matrix and launch on clean machines without Python or Qt installed, with Git installed separately. Exercise real desktop rendering under Linux X11/Wayland, Windows, and macOS; test paths containing spaces and non-ASCII characters. Validate install, upgrade, uninstall, launcher icons, and retained settings.
- Run the packaged executable with `--smoke-test` from outside the source checkout with `PYTHONPATH` unset. It creates a temporary repository and verifies Git, window rendering, fonts, icons, and an actual Git credential request through the bundled helper. Its credential test uses only localhost and fake credentials. On Linux, also extract the `.deb` and run its contained executable. `--version` provides the application version.
- Verify real HTTPS/SSH hosting-provider authentication, locked keys/keychains, cancellation, and Git minimum-version compatibility on each platform. Test merge details with the supported minimum Git version; the implementation uses the [Git 2.30 first-parent diff options](https://git-scm.com/docs/git-log/2.30.0#_diff_formatting).
- For macOS distribution, build with `GITQT_CODESIGN_IDENTITY` set to the publisher's Developer ID, verify nested executables/libraries, notarize the final application/DMG with the publisher's Apple credentials, and staple/validate the ticket. For Windows, Authenticode-sign the application/helper and final installer with the publisher's certificate. Validate signatures on clean hosts. No credentials or certificates belong in the repository. Recompute checksums after signing/notarization.
- Include this project's corresponding source archive and build scripts, dependency versions, `LICENSE`, `THIRD_PARTY_NOTICES.md`, and matching notices/source for bundled dependencies. Audit the actual Qt plugins and native libraries for each platform before public distribution. Generic upstream URLs and the automated wheel-license inventory alone do not fulfill a complete corresponding-source release.
- Publish release notes, platform requirements, trusted checksums/signatures, and a download location only after these gates pass. The project currently has no automatic updater or configured public release channel.

## Performance baseline

Run `python scripts/benchmark_history.py` to generate an isolated 5,000-commit linear repository. Broad smoke budgets are: first 100 rows under 2 seconds, next 100 rows under 2 seconds, 5,000-row in-memory graph layout under 1 second, and traced Python allocations under 64 MiB. On the local Linux run: **81.6 ms**, **27.8 ms**, **47.3 ms**, and **5.0 MiB**, respectively. These are warm local-filesystem measurements, not end-to-end Qt rendering or total process RSS guarantees.

History loads new pages against captured commit tips and caps the displayed graph at 5,000 rows. Caches have entry limits; diff-related caches hold at most 16 entries. Diff output is capped at 2 MB and rendering at 10,000 lines; larger results show an explanation instead of an actionable truncated patch. Whole-file staging remains available. Other captured Git output is capped at 64 MB. Profiling very large branching histories, slow/network filesystems, and hundreds of thousands of refs remains future work.
