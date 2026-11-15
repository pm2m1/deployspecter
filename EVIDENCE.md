# Research evidence: size, bundles and verification

The research evidence lives in git under `docs/experimental_mvp/` on `research/real-application-mvp`. Nothing has
been moved or deleted. The bundles described here are an additional, compact way to download it.

## Size at tag `research-2026-11-15` (commit `493b9ae`)

The tag holds 362.3 MB in 8,554 files. `docs/` accounts for 355.0 MB (7,922 files), and
`docs/experimental_mvp/` for 7,848 of those files.

The largest directories, with sizes that include their subdirectories:

| MB | Files | Directory |
|---|---|---|
| 132.5 | 1,816 | `docs/experimental_mvp/results` |
| 120.3 | 2,014 | `docs/experimental_mvp/nonblind_corpus` |
| 70.2 | 983 | `docs/experimental_mvp/nonblind_corpus/nbc2` |
| 28.5 | 560 | `docs/experimental_mvp/nonblind_corpus/track_b` |
| 24.4 | 438 | `docs/experimental_mvp/confirmatory` |
| 24.1 | 889 | `docs/experimental_mvp/muse_contrib` |
| 23.3 | 825 | `docs/experimental_mvp/muse_contrib/run_1` |
| 22.0 | 82 | `docs/experimental_mvp/results/simultaneous_mutant_compete_2` |
| 18.4 | 980 | `docs/experimental_mvp/reliability` |
| 15.1 | 384 | `docs/experimental_mvp/historical` |
| 15.1 | 383 | `docs/experimental_mvp/historical/chancy_2895c4a` |
| 11.4 | 481 | `docs/experimental_mvp/rolling_upgrade` |
| 11.4 | 481 | `docs/experimental_mvp/rolling_upgrade/procrastinate_3.3_3.4` |
| 8.4 | 254 | `docs/experimental_mvp/nonblind_corpus/saq` |
| 6.9 | 82 | `docs/experimental_mvp/results/simultaneous_mutant_compete_5` |
| 6.9 | 81 | `docs/experimental_mvp/results/simultaneous_mutant_compete_4` |
| 6.8 | 82 | `docs/experimental_mvp/results/simultaneous_explore_compete_4` |
| 6.8 | 81 | `docs/experimental_mvp/results/simultaneous_explore_compete_3` |
| 6.8 | 141 | `docs/experimental_mvp/nonblind_corpus/chancy` |
| 6.5 | 64 | `docs/experimental_mvp/results/simultaneous_diagnostics_1` |

## Building the bundles

```sh
python tools/package_evidence.py build --tag research-2026-11-15 --out evidence-bundles
```

- **Source:** blobs are read from the tag through git, never from the working tree.
- **Bundles:** one `.tar.zst` per directory of 5 MB or more under `docs/experimental_mvp/`, at depth 1 or 2, plus
  one `rest` bundle. Every file belongs to exactly one bundle.
- **Reproducible:** members are sorted, with mtime 0, uid/gid 0 and mode 0644, and `zstd` runs single-threaded at
  level 3. Two builds in a row gave identical `SHA256SUMS`.
- **Size:** on 2026-11-15 the tag gave 30 bundles, 16 MB in total, for 7,848 files. zstd was v1.5.7.
- **Contents:** each bundle includes `FILES.tsv`, listing every file's path, size, sha256 and git blob id.
  `SHA256SUMS` records the bundle hashes, the tag's commit, the zstd version and the options. `INDEX.json` maps
  bundles to directories.

## Where they go (the owner uploads; nothing has been uploaded)

- **GitHub Release** `research-2026-11-15`: attach every `.tar.zst`, plus `SHA256SUMS` and `INDEX.json`.
- **Zenodo**, optional, for a DOI: deposit the same files, and cite the tag and commit in the description.

## Verifying a download

```sh
shasum -a 256 -c <(grep -v '^#' SHA256SUMS)       # the bundles are the published ones
python tools/package_evidence.py verify --tag research-2026-11-15 --dir evidence-bundles
```

`verify` hashes each file in the bundles the way git does, and compares the result with the blob id committed at
the tag. Those ids already exist in the repository, so a bundle can only pass if every file is byte-identical to
what git holds. It also reports any file at the tag that no bundle contains.

On 2026-11-15, `verify` reported 7,848 of 7,848 files matching and `ok: true`. With one byte appended to one
bundle, it reported `sha256 differs from SHA256SUMS` and `ok: false`. Per-file sha256 values can also be compared
with the evidence's own committed manifests and ledgers (for example `MANIFEST.json`, `SHA256SUMS*` and
`RESULTS_LEDGER.jsonl` files) after unpacking.
