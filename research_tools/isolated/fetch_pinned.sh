#!/bin/bash
# EXPERIMENTAL: download the two pinned third-party sdists into DIR and verify their sha256; any mismatch fails.
# The archives are data: nothing here unpacks, imports or runs them (run_isolated.py re-verifies and unpacks data-only).
#   research_tools/isolated/fetch_pinned.sh DIR
set -euo pipefail
DIR=${1:?usage: fetch_pinned.sh DIR}
mkdir -p "$DIR"
fetch() {  # name url sha256
  local f="$DIR/$1"
  if [ ! -f "$f" ]; then
    curl -fsSL --max-time 120 -o "$f.part" "$2"
    mv "$f.part" "$f"
  fi
  echo "$3  $f" | shasum -a 256 -c - >/dev/null || { echo "sha256 mismatch for $1; removing it" >&2; rm -f "$f"; exit 1; }
  echo "verified $1"
}
fetch pgqueuer-1.6.0.tar.gz https://files.pythonhosted.org/packages/source/p/pgqueuer/pgqueuer-1.6.0.tar.gz \
  ed1306404f96e25c84c951f434e8b06b091e04e0177af0d5441505a7944f156e
fetch chancy-0.26.0.tar.gz https://files.pythonhosted.org/packages/source/c/chancy/chancy-0.26.0.tar.gz \
  ac98b9c502d7bdb6f47d53fac1c22b4dc55e6cfcfec84b08b1a0cbaee45aef06
fetch chancy-0.25.1.tar.gz https://files.pythonhosted.org/packages/source/c/chancy/chancy-0.25.1.tar.gz \
  264ed4f732cb51c405b789e0727c4d045fdfa1dcee1e3eed6d762df6c44122d7
