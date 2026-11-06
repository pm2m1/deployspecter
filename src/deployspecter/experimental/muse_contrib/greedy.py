"""EXPERIMENTAL: the "strong systematic" deterministic orderings (arms B2, B4): greedy pairwise coverage.

From a full grid of candidates, each described by a tuple of feature values, repeatedly take the candidate that
covers the most not-yet-covered (feature i = a, feature j = b) pairs; ties go to the earliest in grid order. This is
the standard combinatorial-testing construction; it encodes no knowledge of any defect.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from typing import Any


def pairs(features: Sequence[Any]) -> set[tuple[int, Any, int, Any]]:
    return {(i, features[i], j, features[j]) for i, j in itertools.combinations(range(len(features)), 2)}


def order[T](grid: list[T], features: Callable[[T], Sequence[Any]], limit: int | None = None) -> list[T]:
    left = list(grid)
    covered: set[tuple[int, Any, int, Any]] = set()
    out: list[T] = []
    while left and (limit is None or len(out) < limit):
        best = max(range(len(left)), key=lambda k: (len(pairs(features(left[k])) - covered), -k))
        covered |= pairs(features(left[best]))
        out.append(left.pop(best))
    return out
