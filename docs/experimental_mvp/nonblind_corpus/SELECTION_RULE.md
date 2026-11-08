# NONBLIND development corpus `ds-nbc/1`: selection rule (frozen 2026-11-08, before any history scan)

**Label: NONBLIND, DEVELOPMENT ONLY.** Every case in this namespace is chosen and encoded by the developers, who
read the upstream fixes. No case here is blind or confirmatory, and none may enter the confirmatory corpus,
`CORPUS_MANIFEST.json` or the Amendment 003 eligibility rules. This namespace has its own registry,
`REGISTRY_nbc_development.jsonl`. The frozen confirmatory rules are not changed.

## 1. Applications (fixed before any scan)

The scan covers only applications that are **already exposed** to the developers and already have a live,
reviewed integration. Exposing a new application here would remove it from the pool a future curator draws from.

| Application | Why it is admissible | Repository |
|---|---|---|
| chancy | frozen development app; `chancy-sv/1` and the stepped pool | https://github.com/tktech/chancy |
| pgqueuer | development app; Driver-port adapter | https://github.com/janbjorge/pgqueuer |
| procrastinate | frozen development app; stepped pool | https://github.com/procrastinate-org/procrastinate |
| saq | exposed by onboarding pilot 1; pilot adapter | https://github.com/tobymao/saq |
| taskiq-postgresql | exposed by onboarding pilot 1 | **none declared** (its PyPI metadata names no repository): EXCLUDED, no fix pairs can be pinned |

Never in scope: the held-out applications (saleor, healthchecks, netbox, authentik), GitLab or anything derived from
it, sealed labels, and any application a future curator might select.

## 2. Window

Commits reachable from the default branch, from the first commit at which the package declares a psycopg 3
dependency up to 2026-11-05 (the corpus freeze date of `CORPUS_SHORTLIST.json`).

## 3. Candidate commits (mechanical, `research_tools/nbc/scan.py`)

A non-merge commit is a **candidate** when all of these hold:
1. Its message matches the **topic** expression, case-insensitive:
   `\b(race|racy|concurren\w*|lock(s|ing|ed)?|deadlock\w*|duplicat\w*|twice|double|cancel\w*|abort\w*|retr(y|ies|ied)|stuck|stall\w*|lost|lose|atomic\w*|transaction\w*|idempoten\w*|heartbeat\w*|orphan\w*|zombie\w*|overwrit\w*|stale)\b`
2. Its subject matches the **fix** expression, case-insensitive:
   `\b(fix\w*|bug\w*|correct\w*|prevent\w*|avoid\w*|resolv\w*|handl\w*)\b`
3. It changes at least one non-test `.py` or `.sql` file in the package's source tree. Paths under `tests/`,
   `test/`, `docs/` or `examples/`, or named `test_*`, are excluded.

The scan writes `CANDIDATES.json`: commit, date, subject and changed source files for every candidate, plus counts
of commits scanned. The bodies and diffs of the candidates are read only during screening (§4).

## 4. Screening (in this order; the first failing criterion is recorded)

Per application, candidates are screened **most recent first**. At most **12** are screened per application in this
sprint; the rest are recorded as NOT SCREENED (capacity), never silently dropped.

| | Criterion |
|---|---|
| E1 | The fix commit and its first parent build from hash-pinned public `git archive` trees |
| E2 | The defect is database-mediated behaviour reachable through the application's public API. Pure in-process logic, typing, packaging, logging or documentation fixes fail E2 |
| E3 | The code path runs under the application's existing stepping seam at that revision, or under a pinned, declared variant of that adapter. Unsupported drivers or runtimes fail E3; the engine is not extended to raise the count |
| E4 | A documented contract defines the correct behaviour: docs, changelog, docstring, or a maintainers' test in the fix |
| E5 | A developer-written property can decide it from the final database state and the actors' results |
| E6 | Execution stays inside the restricted-container policy: pinned public source, with the fix diff and the code path reviewed; no network; no unreviewed code outside the container |

Fix commits that address one defect form **one case**. Seeds, schedules, replicates, mutants and ablations never
count as separate cases.

## 5. Reproduction (per eligible case)

Done in this order, each attempt recorded whether it succeeds, fails or is refused:
1. Pin the parent (R1, buggy) and fix (R2) trees by tree hash and archive sha256.
2. Record the upstream issue or fix, and the documented deployment contract (single-version unless documented
   otherwise).
3. Write the integration's workload schema, and the deterministic workloads, before the first live run.
4. Write the property (oracle) from the documented contract. It may not mention the fix's code.
5. Run both trees on fresh disposable schemas under the same schedules, isolated and server-log corroborated.

The outcome is one of:
- **REPRODUCED:** at least one SOUND corroborated R1 run violates the property, and every R2 run under the same
  workloads and schedules holds;
- **NOT REPRODUCED:** no R1 violation within the budget (B = 12 schedules per workload);
- **CONTROL FAILED:** an R2 violation;
- **REFUSED:** with the instrument's or the screen's reason.

A case that reproduces a documented upstream defect is never reported as a new finding.

## 6. Exposure

Every case records: who read what (commit message, diff, issue, tests) and when, before which step. This rule
itself was written after `chancy-2895c4a` and procrastinate `8fa4ab5` had been read in earlier work. Those two are
recorded as prior exposure, and `chancy-2895c4a` remains the corpus's existing case.
