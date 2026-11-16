# Register on OSF: click by click (for the owner; not done)

You need an OSF account (https://osf.io, sign in with ORCID or email). Nothing in the repository changes until step 13.
After step 11, `FREEZE.json` and everything it hashes must never change.

## Before you start

1. Pull `research/benchmark-prep` and confirm the freeze still matches:
   ```sh
   .venv/bin/python research_tools/solo/build_freeze.py --check     # must print "FREEZE.json matches a rebuild"
   shasum -a 256 docs/experimental_mvp/osf/FREEZE.json             # the value you paste in step 8
   ```
   As committed on 2026-11-17 (spec format 2, coverage reporting, the strict-subset and detection-level sensitivity
   analyses), it must print
   `6fb0a189eb68c2351203bb5764387432c824e20d11a117a389dcdacb9aeced47`. A different value means something hashed changed
   after this file was written. Stop, and find out what before registering.
2. Confirm the sealed seeds exist and match FREEZE.json, without printing them:
   ```sh
   python3 -c 'import json,hashlib,pathlib; s=json.loads((pathlib.Path.home()/".deployspecter-sealed/seeds.json").read_text()); f=json.load(open("docs/experimental_mvp/osf/FREEZE.json"))["sealed_seeds"]; print(all(hashlib.sha256(s[k].encode()).hexdigest()==f[k+"_sha256"] for k in ("selection_seed","mutant_seed")))'
   ```
   It must print `True`. Keep a copy of `~/.deployspecter-sealed/` in the encrypted backup; without it nobody can
   reproduce the selection.

## On OSF

3. Go to https://osf.io/dashboard → **Create new project**. Title: "DeploySpecter r3 evaluation (single
   investigator)". Leave it **private** for now.
4. In the project → **Files** → **OSF Storage** → **Upload**. Upload the files listed at the top of
   `PREREGISTRATION.md`, including `FREEZE.json`.
5. In the project's top menu → **Registrations** → **New registration**. Choose the template **"OSF
   Preregistration"** → **Create draft**.
6. Fill each field from `PREREGISTRATION.md`: the `###` headings there are the field names, in order. Under
   "Existing data", choose **"Registration prior to creation of data"**.
7. Where the form asks for files (the "Study information" and "Analysis plan" pages have file pickers), attach
   `FREEZE.json`, `AMENDMENT_003_ADOPTION_CANDIDATE_r3.md` and `ANALYSIS_PLAN_SOLO.md` from the project's storage.
8. In **"Other" → "Other"**, add this line at the top:
   `FREEZE.json sha256: <the value from step 1>; sealed seeds: selection <selection_seed_sha256>, mutants
   <mutant_seed_sha256> (from FREEZE.json).`
9. **Review** every page, then **Register**. Choose **"Make registration public immediately"**, or an embargo of up
   to 4 years if you prefer. The registration is timestamped by OSF either way.
10. Wait for the confirmation email. The registration gets its own URL (`https://osf.io/xxxxx`) and, once public, a
    DOI.
11. Download the registration's PDF (registration page → **⋮** → **Download**) and keep it with the backup.

## Back in the repository

12. Record the registration, which unlocks `select_cases.py`:
    ```sh
    python3 - <<'EOF'
    import hashlib, json, pathlib
    f = pathlib.Path("docs/experimental_mvp/osf/FREEZE.json")
    pathlib.Path("docs/experimental_mvp/osf/REGISTRATION.json").write_text(json.dumps({
        "osf_url": "https://osf.io/xxxxx",            # your registration URL
        "registered_at": "YYYY-MM-DDTHH:MM:SSZ",      # from the confirmation
        "freeze_sha256": hashlib.sha256(f.read_bytes()).hexdigest()}, indent=1) + "\n")
    EOF
    ```
13. Fill the two blanks at the end of `protocol/AMENDMENT_003_r3_SOLO_ADOPTION.md` (the OSF URL and the FREEZE
    sha256) **in a new file**, `protocol/AMENDMENT_003_r3_SOLO_REGISTERED.md`, so that the hashed record stays
    unchanged. Commit both new files and push.
14. Optional, a second timestamp: `ots stamp docs/experimental_mvp/osf/FREEZE.json` (OpenTimestamps) and commit the
    `.ots` file.

Then follow `benchmark_prep/RUNBOOK.md`.
