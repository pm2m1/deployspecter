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
# Commit-pinned Chancy trees for the historical case chancy-2895c4a (only with --with-git): a blobless clone, the
# commit's git tree verified, then `git archive` of chancy/ verified by sha256 (deterministic for a commit).
if [ "${2:-}" = "--with-git" ]; then
  REPO_DIR="$DIR/chancy_git"
  [ -d "$REPO_DIR" ] || git clone -q --filter=blob:none --no-checkout https://github.com/tktech/chancy.git "$REPO_DIR" \
    || { echo "git clone failed" >&2; exit 1; }
  snap() {  # short commit tree tar_sha256
    t=$(git -C "$REPO_DIR" rev-parse "$2^{tree}") || exit 1
    [ "$t" = "$3" ] || { echo "tree mismatch for $2: $t" >&2; exit 1; }
    f="$DIR/chancy-git-$1.tar"
    git -C "$REPO_DIR" archive --format=tar -o "$f" "$2" chancy || exit 1
    echo "$4  $f" | shasum -a 256 -c - >/dev/null || { echo "sha256 mismatch for $f; removing it" >&2; rm -f "$f"; exit 1; }
    echo "verified chancy-git-$1.tar (tree $3)"
  }
  snap 80f570f 80f570f0b8332212e46c3c27af5940946f6486a9 a5a389c45c8db1f53275a6c121bb3bf520796adb \
    8747cdc8258aa46d445c82babcdf513c6c8d8e44fae5c67d11c9caffc795deaa
  snap 2895c4a 2895c4a7fc173cec3d645221f63c4e51e2cf7e84 bdf46b6b445bdc8519a3c96133a411610fc4a57a \
    cbc461d2e7cb5edcc48830470405c1dfea91fe5c68381afd6bade26a193ed626
fi
