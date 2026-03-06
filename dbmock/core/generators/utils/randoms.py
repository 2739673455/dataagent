"""Random helper functions."""

from __future__ import annotations

from random import Random
from typing import Sequence, TypeVar

T = TypeVar("T")


def pick(rng: Random, values: Sequence[T]) -> T:
    return values[rng.randrange(0, len(values))]

