# Three messages for the owner to send (drafts; NOT SENT)

Send each from your own account. Attach the named file, or link it on GitHub once `research/benchmark-prep` is
pushed: `https://github.com/pm2m1/deployspecter/blob/research/benchmark-prep/docs/experimental_mvp/<path>`.
Replace the bracketed names.

## 1. Labmate 1, the curator

**Subject:** Would you curate the bug set for my DeploySpecter study?

Hi [name],

I'm setting up a small preregistered study of DeploySpecter, and I need someone I trust to choose the test cases
so that I stay blind to them. The job is to pick 10 or more real, already-fixed database bugs from open-source
Python applications, using a fixed selection rule, and to seal each one so that I can't tell which version is
buggy. A second labmate will write the correctness checks without seeing the fixes, and you'd hand each case to
them before sealing it.

It's roughly 30–45 hours over 3–5 weeks, on your own machine. You'd need to keep everything about the cases from me
until scoring is over. The attached one-page guide (`benchmark_prep/CURATOR_QUICKSTART.md`) covers the steps, the
rules and what never to send me.

Would you be up for it? Happy to walk you through the tooling first. And if not, no problem at all.

Thanks,
Parth

## 2. Labmate 2, the oracle writer

**Subject:** Would you write the correctness checks for my DeploySpecter study?

Hi [name],

I'm setting up a small preregistered study of DeploySpecter. For each test case, I need someone who never sees the
bug fix to write the correctness checks (an "oracle") from the application's public documentation and API alone.
[Labmate 1] will choose and seal the cases and send you, for each one, the application, one version of its source,
its documentation and a short note on which documented behaviour to check. You'd never see the fix, the issue or
the changelog.

It's about 1–2 hours per case, so roughly 10–25 hours over 3–5 weeks for 10–12 cases, plus a few hours reviewing
results at the end. The attached one-page guide (`benchmark_prep/ORACLE_WRITER_QUICKSTART.md`) lists what you'd
receive, what to write, and what you must never look at.

Would you be willing? Happy to go through an example from my earlier, non-blind study first. And if not, no problem
at all.

Thanks,
Parth

## 3. Faculty member, the protocol reader

**Subject:** Would you read a short study protocol before I preregister it?

Use the text of `benchmark_prep/FACULTY_NOTE.md` as the body. Attach the protocol
(`protocol/AMENDMENT_003_ADOPTION_CANDIDATE_r3.md`) and the adoption record
(`protocol/AMENDMENT_003_r3_OWNER_ADOPTION.md`), or link them.
