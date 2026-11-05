# sequential_baseline_repro_4: final evidence at `d3630b5` (NONBLIND)

This set is part of the final simultaneous mixed-version evidence. All its runs are SOUND: isolated, coordinated to completion and corroborated against the server log. The results, their interpretation and the caveats are in `docs/experimental_mvp/simultaneous/RESULTS.md`.

Reproduce:

```bash
python research_tools/isolated/run_isolated.py chancy-mixed --chancy-old-sdist SDISTS/chancy-0.25.1.tar.gz --chancy-sdist SDISTS/chancy-0.26.0.tar.gz --out NEW_DIR
```
