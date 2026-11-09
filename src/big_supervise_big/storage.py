"""Durable sidecar events and sealed patches; never store request headers."""

import hashlib
import json
import os
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class Journal:
    def __init__(self, path: Path, *, secret: str = ""):
        self.path, self.secret = path, secret
        self.events = []

    def append(self, event):
        # A provider may echo a credential in an error; redact before disk and memory.
        encoded = canonical(event)
        if self.secret:
            encoded = encoded.replace(self.secret, "[REDACTED]")
        clean = json.loads(encoded)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.events.append(clean)


def seal_patch(output: Path, patch: str):
    path = output / "model.patch"
    path.write_bytes(patch.encode("utf-8"))
    seal = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    atomic_json(output / "patch-seal.json", seal)
    return seal


def verify_patch(output: Path):
    patch = output / "model.patch"
    seal = json.loads((output / "patch-seal.json").read_text(encoding="utf-8"))
    if seal != {"sha256": hashlib.sha256(patch.read_bytes()).hexdigest(), "bytes": patch.stat().st_size}:
        raise ValueError("Patch seal mismatch")
    return patch
