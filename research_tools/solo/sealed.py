"""Solo r3 design: the sealed seeds and the sealed store.

The seeds live outside the repository (default ``~/.deployspecter-sealed/seeds.json``, mode 600). Only their sha256
is committed (``docs/experimental_mvp/osf/FREEZE.json`` ``sealed_seeds``). ``load_seed`` refuses a seed whose hash
isn't the committed one, so a seed can't be swapped after the freeze.

Sealing is a commitment device for one investigator, not secrecy against them: the investigator holds the seed file.
The store encrypts each record with a keystream derived from the seed (HMAC-SHA256 in counter mode) so that mutants
can't be read by accident (``grep``, an editor, a diff), and every read goes through a command that logs and checks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

DEFAULT_SEEDS = Path.home() / ".deployspecter-sealed" / "seeds.json"


class SealError(Exception):
    pass


def seed_sha256(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def load_seed(name: str, expected_sha256: str, path: Path = DEFAULT_SEEDS) -> str:
    """The seed ``name`` (``selection_seed`` or ``mutant_seed``), refused unless its sha256 is the committed one."""
    try:
        doc = json.loads(Path(path).read_text())
    except FileNotFoundError as e:
        raise SealError(f"no sealed seed file at {path}") from e
    seed = doc.get(name)
    if not isinstance(seed, str) or seed_sha256(seed) != expected_sha256:
        raise SealError(f"{name} isn't the committed seed (sha256 mismatch)")
    return seed


def keystream(key: bytes, label: str, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, f"{label}:{counter}".encode(), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def seal(seed: str, label: str, plaintext: bytes) -> bytes:
    """Encrypt and authenticate ``plaintext`` under ``seed`` for record ``label``."""
    key = hashlib.sha256(b"ds-sealed-store/1:" + seed.encode()).digest()
    body = bytes(a ^ b for a, b in zip(plaintext, keystream(key, label, len(plaintext)), strict=True))
    tag = hmac.new(key, label.encode() + b"\0" + body, hashlib.sha256).digest()
    return b"DSSEAL1" + tag + body


def unseal(seed: str, label: str, blob: bytes) -> bytes:
    if not blob.startswith(b"DSSEAL1"):
        raise SealError("not a sealed record")
    key = hashlib.sha256(b"ds-sealed-store/1:" + seed.encode()).digest()
    tag, body = blob[7:39], blob[39:]
    if not hmac.compare_digest(tag, hmac.new(key, label.encode() + b"\0" + body, hashlib.sha256).digest()):
        raise SealError("sealed record failed authentication (wrong seed, label or a modified file)")
    return bytes(a ^ b for a, b in zip(body, keystream(key, label, len(body)), strict=True))


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
