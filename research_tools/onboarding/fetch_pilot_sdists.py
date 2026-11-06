"""EXPERIMENTAL: fetch the onboarding pilot's pinned sdists into DIR, verified by sha256 (data only: nothing here
unpacks, imports or runs them). A mismatch leaves ``NAME.part`` in place and exits 1; nothing is deleted.

    python -I research_tools/onboarding/fetch_pilot_sdists.py DIR
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

PINS = {
    "saq-0.26.5.tar.gz": (
        "https://files.pythonhosted.org/packages/2b/c1/22381822245402fbff14a990e76a6a8373312e26bf0fdaaba81db27f45c6/"
        "saq-0.26.5.tar.gz", "75e8664ca1351f46a400d28d0441612be4feb6c51ab206cdfad9dc06fcb42acc"),
    "taskiq_postgresql-0.4.0.tar.gz": (
        "https://files.pythonhosted.org/packages/e1/1a/dc903813bc238fcba16624e22f959e681f96bd210583a98e77c1d1c7607c/"
        "taskiq_postgresql-0.4.0.tar.gz", "3e8cda663ec2893adfcf2d1f30447b2c03cc46067622e1f9e1c9a45f4e22731f"),
    "taskiq-0.13.0.tar.gz": (
        "https://files.pythonhosted.org/packages/09/c6/a6e5a15158e1db35b458f68f40b3e44c32134d7edad6bfcdb5bbd4128174/"
        "taskiq-0.13.0.tar.gz", "7d56609a6c5a20b0c9da67e9f310ec9640266f620b8f81880bccd5f16913e687"),
}


def main() -> int:
    d = Path(sys.argv[1])
    d.mkdir(parents=True, exist_ok=True)
    rc = 0
    for name, (url, want) in PINS.items():
        f = d / name
        if not f.exists():
            part = d / (name + ".part")
            with urllib.request.urlopen(url, timeout=120) as r:
                part.write_bytes(r.read())
            if hashlib.sha256(part.read_bytes()).hexdigest() != want:
                print(f"sha256 MISMATCH for {name}; left {part}", file=sys.stderr)
                rc = 1
                continue
            part.rename(f)
        got = hashlib.sha256(f.read_bytes()).hexdigest()
        print(f"{'verified' if got == want else 'MISMATCH'} {name} {got}")
        rc |= got != want
    return rc


if __name__ == "__main__":
    sys.exit(main())
