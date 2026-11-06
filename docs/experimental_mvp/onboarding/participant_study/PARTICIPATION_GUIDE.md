# Participation guide (short)

1. **Before you start.**
   - Read and sign the consent text. You get a study id, such as P07, and your condition order.
   - Never type your name into any tool.
2. **Set up** with `ENVIRONMENT_SETUP.md`. The prepared machine already has the pinned sources and the disposable
   database; you never use another database.
3. **For each library:**
   - `pilot.py start --operator <study id> --operator-kind independent`;
   - work toward S1 (a valid workload), then S2 (a corroborated run), then S3 (the correctness check);
   - record every manual action with `pilot.py note … intervention`, as it happens;
   - run checks with `pilot.py validate` and `pilot.py run`, at most 10 runs per library. Library code runs only
     through `run`;
   - in the Muse condition only, use `pilot.py muse`, at most 3 requests per library;
   - finish with `pilot.py end` when you reach S3, run out of budget, or decide to stop.
4. **Don't:**
   - read the other condition's materials;
   - use outside AI tools;
   - copy code between the two libraries;
   - change any file outside the declared adapter paths.
5. **Afterwards:** hand back the session directories. A scorer who doesn't know your condition scores the outcomes.
