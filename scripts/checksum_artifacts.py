"""Write SHA-256 sidecars for all final artifacts, after signing if applicable."""

import hashlib
from pathlib import Path


def main():
    dist = Path(__file__).resolve().parents[1] / "dist"
    for path in [*dist.glob("*"), *(dist / "releases").glob("*")]:
        if not path.is_file() or path.suffix not in {".whl", ".gz", ".zip", ".deb", ".exe", ".dmg"}:
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")


if __name__ == "__main__":
    main()
