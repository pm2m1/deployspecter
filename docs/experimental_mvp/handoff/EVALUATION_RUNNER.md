# Evaluation runner brief

**Purpose:** run the frozen arms on the frozen cases, and record everything, without access to labels.

1. **Receive only permitted context:**
   - pinned sources (sdist sha256 and tag commit);
   - the trusted registry and schema;
   - the public `SEAL.json`;
   - the frozen prompts and budgets.
2. **Freeze before running:** the model, reasoning effort, prompt hashes, schemas, budgets B/T/R/P/C and the deterministic generators (draft v2 §4 and §12). The curator's leak check must pass on exactly the frozen inputs.
3. **Run every arm at equal budget.** Record every proposal, rejection, chain outcome, run outcome, corroboration and cost (draft v2 §5). Save every model reply on arrival. Failed calls count.
4. **Don't inspect** hidden labels, the reveal file or oracle verdicts beyond what scoring requires, until authorized scoring.
5. **Hand over** the sealed result bundle, with hashes, to scoring. The curator reveals, and `seal_labels.py verify` confirms the commitment before detections are scored.
