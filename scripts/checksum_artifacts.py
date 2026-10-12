"""Write or verify SHA-256 sidecars for final release artifacts."""

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_SUFFIXES = (".whl", ".tar.gz", ".zip", ".deb", ".exe", ".dmg")


def release_artifacts(dist: Path) -> list[Path]:
    candidates = [*dist.glob("*"), *(dist / "releases").glob("*")]
    return sorted(
        path for path in candidates if path.is_file() and path.name.endswith(ARTIFACT_SUFFIXES)
    )


def checksum_line(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return f"{digest}  {path.name}\n"


def write_checksums(dist: Path) -> list[Path]:
    artifacts = release_artifacts(dist)
    if not artifacts:
        raise RuntimeError(f"No release artifacts found under {dist}")
    sidecars = []
    for path in artifacts:
        sidecar = path.with_name(path.name + ".sha256")
        temporary = sidecar.with_name(sidecar.name + ".tmp")
        temporary.write_text(checksum_line(path), encoding="ascii")
        temporary.replace(sidecar)
        sidecars.append(sidecar)
    return sidecars


def verify_checksums(dist: Path) -> list[Path]:
    artifacts = release_artifacts(dist)
    if not artifacts:
        raise RuntimeError(f"No release artifacts found under {dist}")
    expected_sidecars = {path.with_name(path.name + ".sha256") for path in artifacts}
    actual_sidecars = {
        *dist.glob("*.sha256"),
        *(dist / "releases").glob("*.sha256"),
    }
    orphaned = sorted(actual_sidecars - expected_sidecars)
    if orphaned:
        names = ", ".join(str(path.relative_to(dist)) for path in orphaned)
        raise RuntimeError(f"Checksum sidecars without matching artifacts: {names}")
    missing = sorted(path for path in expected_sidecars if not path.is_file())
    if missing:
        names = ", ".join(str(path.relative_to(dist)) for path in missing)
        raise RuntimeError(f"Artifacts without checksum sidecars: {names}")
    invalid = [
        path
        for path in artifacts
        if path.with_name(path.name + ".sha256").read_text(encoding="ascii") != checksum_line(path)
    ]
    if invalid:
        names = ", ".join(str(path.relative_to(dist)) for path in invalid)
        raise RuntimeError(f"Checksum verification failed: {names}")
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify", action="store_true", help="verify existing sidecars without changing them"
    )
    args = parser.parse_args()
    paths = verify_checksums(ROOT / "dist") if args.verify else write_checksums(ROOT / "dist")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
