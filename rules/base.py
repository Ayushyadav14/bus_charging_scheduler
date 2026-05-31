"""Plugin interfaces for hard and soft rules, plus the small data objects the
engine hands them.

Two candidate shapes exist because rules act at two moments:
* ``PlanCandidate`` — a whole charging plan, validated by HARD rules.
* ``PickCandidate`` — one waiting bus at a contended charger, scored by SOFT
  rules to decide who charges next.

Hard rules answer "is this legal?"; soft rules answer "how good a choice is
this?" (lower raw value = serve sooner; the engine normalises and weights them).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PlanCandidate:
    """A proposed charging plan for one bus (ordered station ids)."""
    bus: "object"
    stations: tuple


@dataclass
class PickCandidate:
    """One bus waiting at a charger, considered for the next slot."""
    bus: "object"
    station_id: str
    time: int
    wait_so_far: int
    remaining_distance: float


@dataclass
class SchedulingContext:
    """Read-mostly world state a rule may consult. Carries the live aggregates
    that make soft rules *stateful* (e.g. how many charges each operator has
    had so far), which is what lets weights change later decisions."""
    scenario: "object"
    operator_completions: dict = field(default_factory=dict)  # operator -> charges done


class HardRule(ABC):
    """A hard constraint. ALL hard rules must pass for a plan to be legal."""
    name: str = "hard-rule"

    @abstractmethod
    def is_valid(self, candidate: PlanCandidate, context: SchedulingContext) -> bool:
        ...


class SoftRule(ABC):
    """A tunable preference. Returns a raw scalar where LOWER means "serve this
    bus sooner". The engine min-max normalises raw values across the waiting set
    and multiplies by the weight named in ``weight_key`` before summing."""
    name: str = "soft-rule"
    weight_key: str = "overall"

    @abstractmethod
    def cost(self, candidate: PickCandidate, context: SchedulingContext) -> float:
        ...