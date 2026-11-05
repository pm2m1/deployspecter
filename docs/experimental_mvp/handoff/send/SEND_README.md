# Sending the v3 handoff packages (instructions for the owner; nothing has been sent)

**Nothing here has been sent to anyone, and no recipient is named.** The owner chooses each person, checks that
they are independent (`templates/participation_declaration.md`), and sends the bundle themselves.

## Before sending
1. Confirm the candidate: `shasum -a 256 docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md` must
   print `ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0`.
2. Verify the bundle from the repository root:
   `shasum -a 256 -c docs/experimental_mvp/handoff/send/SHA256SUMS_curator_v3` (or `SHA256SUMS_oracle_v3`).
   Every line must say OK. If a file changed since the list was made, regenerate the list and review the change
   before sending.
3. Recommended: decide on the owner-form gap first (`protocol/OWNER_DECISIONS.md`, "A gap in the forms").
4. Send only the files listed below, at their repository paths, as one archive or as a link to this commit. Never
   send results directories, ledgers or reveal files.
5. Record the send date, the bundle and its checksum file against adoption step 1 in
   `protocol/approval/ADOPTION_CHECKLIST.md`, without the recipient's private details.

## Curator bundle (SHA256SUMS_curator_v3)
```
21cc3dc6b88f6819d936aaa412d439eba10ec4de8536eeb1e1d88f43ecdc77b9  docs/experimental_mvp/handoff/send/CURATOR_PACKAGE_v3.md
175b46da63f7e2191257b38f5e0eb5cef48ff59600c69e619e7e34e72d26c3db  docs/experimental_mvp/handoff/send/OVERVIEW.md
fbab530ade66efb636b2525c4a50882f5d264c7968a098e07bfc87f861a4c6ef  docs/experimental_mvp/handoff/send/CURATOR_PACKAGE_v2.md
1744bd438a0d6b9a386862200efe5d87b48e6a4ab43a687a6c09de9a110e38cf  docs/experimental_mvp/handoff/send/EVALUATOR_ACCESS_v3.md
3d239f58a6dbbfa966b842ee36e239784b05315f65d6ba749d31de280f7ad4b6  docs/experimental_mvp/handoff/templates/participation_declaration.md
ced262114f944f8eda3c7cad4691d41ff7dd593692401c7236905c71b945f189  docs/experimental_mvp/handoff/templates/labels.json
ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0  docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md
d9b346362bb1e6ed3be8ed9738eaed2f6067dbdd966159c1edc2e2e2dd25d37e  docs/experimental_mvp/protocol/approval/CANDIDATE_SHA256
7316ff86bafae056d6dcb59c8894e215ebac05d64b831e0195bddef7ebccd74b  docs/experimental_mvp/protocol/approval/CURATOR_ACCEPTANCE.md
fc6136a4c4de9ee2ccd97ef7bbb107567bbfa03acd4934972845cb0580c32a4a  docs/experimental_mvp/protocol/OWNER_DECISIONS.md
f8d4a243f5fe3e01911193aedb000db40e94b77912eef0a579ff0c8669ab8ab8  docs/experimental_mvp/CORPUS_MANIFEST.json
3eaa449eca5ca944e1b07a58ce3c6e36e114636b88d8c4e82a686812c8a808f5  research_tools/corpus_select.py
92d926781731dd97d59da5e27adb7fd81b6a4280264313aa90bf3fbe61fcd5a6  research_tools/corpus_intake.py
81b31596cd99ce8967bc17fb4f69006494de5b04e0d94ca10dacdb92d12e68a1  research_tools/seal_labels.py
e1cd4e6e7aa880d4cc9d322974c8b001b7c36c15d4b2d633631fea4542a1cccb  research_tools/sealed_evaluation.py
6ea8ed9bfcf2689b8d69912bf85bb00982d84487d2e0d681043603eb15f32c88  research_tools/evaluator_sandbox.py
```

## Oracle author and reviewer bundle (SHA256SUMS_oracle_v3)
```
e083cb711a9b871c8c7461e9b30a21afd0750228db85480954650e91166007a4  docs/experimental_mvp/handoff/send/ORACLE_REVIEW_PACKAGE_v3.md
175b46da63f7e2191257b38f5e0eb5cef48ff59600c69e619e7e34e72d26c3db  docs/experimental_mvp/handoff/send/OVERVIEW.md
5644a7f91b55d8617c4f2a3839c2f4b55b4aedcaffe1de003368308522a3ce35  docs/experimental_mvp/handoff/send/ORACLE_REVIEW_PACKAGE_v2.md
1744bd438a0d6b9a386862200efe5d87b48e6a4ab43a687a6c09de9a110e38cf  docs/experimental_mvp/handoff/send/EVALUATOR_ACCESS_v3.md
3d239f58a6dbbfa966b842ee36e239784b05315f65d6ba749d31de280f7ad4b6  docs/experimental_mvp/handoff/templates/participation_declaration.md
2e30251e37b0435147af9ba8a6ce0c9e712ae83cb31b19edb606a47055794a7c  docs/experimental_mvp/handoff/templates/oracle_spec.md
4f10e2ce7e05da4d269aac9d1eef6b858018375ac027baaa412c4a7bac8c79d1  docs/experimental_mvp/handoff/templates/triage_record.json
a342de4350b4517a97b75e9eaa729bdd4b5bf8b974c6ab46d7a3d8ef66388704  docs/experimental_mvp/historical/chancy_2895c4a/CASE.md
775685f1ea256a8fdb6e65501bcb1c19b7fa5dfc84f00c4b4eb61184acca8520  docs/experimental_mvp/rolling_upgrade/procrastinate_3.3_3.4/CASE.md
dc7dfc4490cced247fc1b4610f34f85c417012f72cdd43328f71c3334bafbe61  docs/experimental_mvp/rolling_upgrade/procrastinate_3.3_3.4/ERRATA.md
d701a2f4c5150c8874cfe1ca514fab1f88ba0af15e266a6d5c67d1398222d4cd  docs/experimental_mvp/simultaneous/PROPERTIES.md
ad5ea33111ac56e1f7f65228e770856095538e9fe013a514d16a1f0b9b246cd0  docs/experimental_mvp/protocol/AMENDMENT_003_ADOPTION_CANDIDATE.md
d9b346362bb1e6ed3be8ed9738eaed2f6067dbdd966159c1edc2e2e2dd25d37e  docs/experimental_mvp/protocol/approval/CANDIDATE_SHA256
11767761327ca31842815c90473395bb225518e9d881e343d9f68c7afbb60b8d  docs/experimental_mvp/protocol/approval/ORACLE_REVIEWER_ACCEPTANCE.md
```

The triage reviewers' bundle (`OVERVIEW.md`, the declaration, `templates/triage_record.json` and
`TRIAGE_REVIEWERS_ACCEPTANCE.md`) can be assembled the same way. It is not prepared here, because this round
covers only the curator and oracle roles.
