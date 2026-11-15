<!-- DRAFT for the owner to review and post as a GitHub Discussion (category: Q&A) on TkTech/chancy. Not posted.
     It is a question, not a bug report. Paste from the title line down. -->

**Title:** Are 0.25.x and 0.26.0 workers supported running at the same time during an upgrade?

Hi, and thanks for Chancy. I have a question about upgrading from 0.25.1 to 0.26.0.

The 0.26.0 changelog says to stop your workers, then run `chancy.migrate()`. In #38 you describe scaling every
service to 0 before running `chancy misc migrate`. I couldn't find anything about whether workers of the two
versions may overlap, for example during a rolling deploy where some 0.25.1 workers are still draining after the
migration and 0.26.0 workers have started. Is that supported, or should the old workers always be fully stopped
first?

The reason I ask: I ran a small experiment that is **outside the documented procedure**. I migrated to 0.26.0's
schema, then ran a 0.25.1 worker and a 0.26.0 worker against the same database at the same time, each in its own
container, and cancelled a running job from one version while the other version ran it. Notifications were
disabled, and 0.25.1's `cancel_job` says they're required to cancel a running job, so the 0.25.1 cells may simply
be unsupported. I checked every statement against the PostgreSQL server log.

| Cancel issued by | Job run by | Cancel kept? |
|---|---|---|
| 0.26.0 | 0.26.0 | yes: the job stays cancelled (`failed`, attempts 0) |
| 0.26.0 | 0.25.1 | no: 0.25.1's completion overwrote it (3 of 3 repeats) |
| 0.25.1 | 0.26.0 | no: 0.26.0's completion overwrote it |
| 0.25.1 | 0.25.1 | no, which matches the race your 0.26.0 changelog says was fixed |

This is a non-blind result from my own research harness. I wrote the scenarios after reading the code and the
changelog, and I'm not claiming a bug. My reading is that 0.26.0's cancellation guarantee holds when both sides are
0.26.0, as documented, and that mixed versions are where it can be lost.

If mixed workers aren't supported, would a short note in the upgrade instructions help? Something like: "Stop all
0.25.x workers, including any still draining, before starting 0.26.0 workers. Cancellations made while both
versions run may be lost." I'm happy to open a PR for the docs if that's useful.

Details and the reproduction:
[`docs/experimental_mvp/simultaneous/RESULTS.md`](https://github.com/pm2m1/deployspecter/blob/research-2026-11-15/docs/experimental_mvp/simultaneous/RESULTS.md)
(§4). [Owner: this link works only after the tag `research-2026-11-15` is pushed. Until then, use the branch link
or remove it.]
