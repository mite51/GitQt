# Third-party notices

GitQt source is licensed GPL-3.0-or-later; see LICENSE. Git is required separately (2.30+) and is not bundled. Standalone distributions bundle the following components without modifying their source:

- Python, under the Python Software Foundation license. Source: https://www.python.org/downloads/source/
- PySide6 and Shiboken6, available under LGPL-3.0 / GPL and commercial terms. Source and licensing: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://doc.qt.io/qtforpython-6/licenses.html
- Qt 6 runtime/plugins, under the applicable LGPL-3.0 / GPL terms, with third-party components identified in Qt's license and attribution files. Source: https://download.qt.io/archive/qt/ and https://doc.qt.io/qt-6/licenses.html
- Inter fonts, SIL Open Font License 1.1; full copyright and license in licenses/Inter-OFL.txt.
- PyInstaller bootloader, GPL with its distribution exception; the matching COPYING.txt is included by the release build. Source: https://github.com/pyinstaller/pyinstaller

Release builds include exact installed Python-distribution versions and their supplied license/notice files under licenses/, plus Python's license. Qt dynamically linked libraries remain separate in the one-directory bundle and can be replaced by compatible builds. Application source and build scripts must accompany binary releases (the release workflow produces a source archive). Before public distribution, the publisher must audit the actual platform-specific bundled libraries and provide matching corresponding source and all applicable third-party notices; the automated inventory is not a substitute for that release audit.
