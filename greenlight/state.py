"""Rollout phases and the transition rules between them."""
from __future__ import annotations
from enum import Enum


class Phase(str, Enum):
    PENDING = "Pending"
    PROGRESSING = "Progressing"
    PROMOTED = "Promoted"
    ROLLING_BACK = "RollingBack"
    ROLLED_BACK = "RolledBack"
    FAILED = "Failed"


# Terminal phases: the controller stops reconciling once here.
TERMINAL = {Phase.PROMOTED, Phase.ROLLED_BACK, Phase.FAILED}


def is_terminal(phase: str | None) -> bool:
    return phase in {p.value for p in TERMINAL}


def next_step_index(current: int, steps: list[int]) -> int | None:
    """Return the next step index, or None if the candidate is fully promoted."""
    nxt = current + 1
    return nxt if nxt < len(steps) else None
