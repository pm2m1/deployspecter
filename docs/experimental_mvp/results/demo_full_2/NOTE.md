# demo_full_2: reading note

This is the full one-command run at revision `2a023f8`. It exited 0, with 267 core experimental tests passing.

**Known issue.** In this record, the Django server-binding and client-binding runs share run IDs (e.g. `7ba226229250`). At that revision the run identity didn't include the registry options that carry the binding mode. The bundle files are named per case, so nothing was overwritten, and the verdicts are unaffected.

**Fix.** The identity was fixed afterwards, with a regression test (`test_the_two_binding_modes_have_distinct_run_identities`). `demo_integrated_django_1` is the re-recorded integrated run with distinct identities.
