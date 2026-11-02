# Amendment 001 to the evaluation protocol

**Status:** in effect from the Git commit that adds this file. It changes protocol version 1 to version 1.1. The frozen file itself is never edited.

- **Number and date:** 001, 2026-11-03.
- **Sections changed:** section 9, "Paired detection" paragraph, one sentence.
- **Reason:** an arithmetic error found immediately after the freeze. Under the Holm correction the protocol itself specifies, the smaller of the two primary p-values must be at most 0.025. Six of 6 gives p = 0.03125, which fails that step.
- **Held-out material or results seen when written:** none. No evaluation arm has run.
- **Affects a primary hypothesis:** no. The test, the correction, the α and the 10-defect minimum don't change. The replaced sentence was an illustration that misstated what they imply. The directional reading below states what "supports" already had to mean, given that P1 and P2 are directional and the test is two-sided.

## Replaced text

```text
With 10 defects, at least 6 discordant pairs all in one direction are needed.
```

## Replacement

```text
Exact two-sided McNemar p-values come from the discordant pairs alone: p = min(1, 2 × P(X ≤ c)), for X ~ Binomial(a + c, 1/2), with a pairs favouring one arm and c the other. Holm orders P1 and P2 by p-value. The smaller must be at most 0.025; only then is the larger compared with 0.05. The smallest result that can pass the first step is 7 discordant pairs with none opposing (p = 0.015625). With opposing pairs, more are needed, for example 9 to 1 (p = 0.021484). Six to 0 (p = 0.03125) can pass only as the second step, after the other primary hypothesis has passed the first. A rejection supports P1 or P2 only if the discordant pairs favour the arm that hypothesis names. A rejection in the other direction is reported as evidence against that hypothesis.
```
