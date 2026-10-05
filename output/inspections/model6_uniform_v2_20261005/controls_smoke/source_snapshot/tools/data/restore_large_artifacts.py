"""Verify or restore byte-exact experiment artifacts stored below Git's file limit."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "output/large_artifacts/manifest.json"


def checksum(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def resolve_relative(base: Path, relative: str) -> Path:
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"Path outside artifact directory: {relative}")
    return path


def restore(entry: dict, verify_only: bool) -> None:
    target = resolve_relative(ROOT, entry["path"])
    expected = (entry["sha256"], entry["size"])
    # Temporary reconstruction never changes an existing local artifact.
    with tempfile.TemporaryDirectory(prefix="restore-experiment-") as temporary:
        encoded = Path(temporary) / "encoded"
        with encoded.open("wb") as output:
            for part in entry["parts"]:
                source = resolve_relative(MANIFEST.parent, part["path"])
                if checksum(source) != (part["sha256"], part["size"]):
                    raise ValueError(f"Part checksum mismatch: {source}")
                with source.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        output.write(block)
        encoding = entry["encoding"]
        if encoding == "gzip":
            reconstructed = Path(temporary) / "original"
            with gzip.open(encoded, "rb") as stream, reconstructed.open("wb") as output:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    output.write(block)
        elif encoding == "identity":
            reconstructed = encoded
        else:
            raise ValueError(f"Unsupported artifact encoding: {encoding}")
        if checksum(reconstructed) != expected:
            raise ValueError(f"Restored checksum mismatch: {target}")
        if verify_only:
            print(f"Verified: {entry['path']}")
            return
        if target.exists():
            if checksum(target) != expected:
                raise ValueError(f"Existing artifact differs; refusing to overwrite: {target}")
            print(f"Already restored: {entry['path']}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_target = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as output, reconstructed.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    output.write(block)
            os.replace(temporary_target, target)
        finally:
            if os.path.exists(temporary_target):
                os.unlink(temporary_target)
        print(f"Restored: {entry['path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Check stored bytes without restoring files")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["version"] != 1:
        raise ValueError("Unsupported artifact manifest version")
    for entry in manifest["artifacts"]:
        restore(entry, args.verify)


if __name__ == "__main__":
    main()
