# simultaneous_mutant_compete_1: a harness crash before any simultaneous phase (kept as a record)

The batch aborted at its first run, in the 0.26.0 pre-phase (migrate). The mutant image's adapter refuses to run unless the mutant is declared (`DS_CHANCY_MUTANT`, carrying the mutated file's hash), and `run_coordinated.py` didn't pass that declaration. The container exited 1, and no simultaneous statement ran. Only the 0.25.1 setup pre-phase's record is here.

**Fix:** every 0.26.0 container of a mutant run now gets the declaration. The batch was re-run as `simultaneous_mutant_compete_2`.
