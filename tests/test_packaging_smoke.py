import tomllib
from pathlib import Path


def test_pyproject_packaging_metadata_has_console_entrypoint() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pyproject = project_root / "pyproject.toml"

    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = payload["project"]
    scripts = project["scripts"]

    assert payload["build-system"]["build-backend"] == "hatchling.build"
    assert project["name"] == "gitqt"
    assert scripts["gitqt"] == "gitqt.main:main"
