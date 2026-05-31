"""Typed in-memory model for the bus charging scheduler.

These dataclasses are the *only* shape the engine and rules ever see. Everything
physical (range, charge time, speed, capacity, distances) lives here as data
loaded from a scenario file — never as a literal in the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
# Physical world
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Physics:
    """Speed/range/charge constants for a scenario. No magic numbers elsewhere."""
    battery_range_km: float
    charge_minutes: int
    speed_kmph: float

    def travel_minutes(self, dist_km: float) -> int:
        """Minutes to cover ``dist_km`` at the scenario speed.

        At 60 km/h this is 1 minute per km, but the conversion is derived from
        ``speed_kmph`` so a different speed is a pure data change.
        """
        return round(dist_km / self.speed_kmph * 60.0)


@dataclass(frozen=True)
class Node:
    """A point on the route: an endpoint (full-charge, not scheduled) or a
    station (has ``chargers`` parallel slots, capacity N)."""
    id: str
    name: str
    kind: str  # "endpoint" | "station"
    chargers: int = 0  # capacity N; 1 today, never hardcoded
    full_charge: bool = False
    attrs: dict = field(default_factory=dict)  # forward-compat per-node fields

    @property
    def is_station(self) -> bool:
        return self.kind == "station"


@dataclass(frozen=True)
class Direction:
    """A direction is just a (from -> to) pair over the canonical node list."""
    id: str
    from_id: str
    to_id: str


@dataclass
class Route:
    """Ordered nodes + symmetric segment distances. Direction is a traversal
    order, never a special case. All distance/feasibility queries are derived."""
    id: str
    nodes: list  # list[Node] in canonical FORWARD order
    seg_km: dict  # frozenset({a_id, b_id}) -> distance_km
    directions: dict  # direction_id -> Direction

    def __post_init__(self):
        self._index = {n.id: i for i, n in enumerate(self.nodes)}

    def order(self, direction_id: str) -> list:
        """Nodes in traversal order for a direction (forward walk or reverse)."""
        d = self.directions[direction_id]
        i, j = self._index[d.from_id], self._index[d.to_id]
        step = 1 if j >= i else -1
        return [self.nodes[k] for k in range(i, j + step, step)]

    def stations_along(self, direction_id: str) -> list:
        """Station nodes (skipping endpoints) in traversal order."""
        return [n for n in self.order(direction_id) if n.is_station]

    def dist_from_origin(self, direction_id: str) -> dict:
        """{node_id: km from this direction's origin}, endpoints included."""
        seq = self.order(direction_id)
        out = {seq[0].id: 0.0}
        acc = 0.0
        for a, b in zip(seq, seq[1:]):
            acc += self.seg_km[frozenset((a.id, b.id))]
            out[b.id] = acc
        return out

    def total_length(self, direction_id: str) -> float:
        seq = self.order(direction_id)
        return self.dist_from_origin(direction_id)[seq[-1].id]

    def feasible_plans(self, direction_id: str, battery_range_km: float) -> list:
        """All valid charging-station subsets for a direction, computed from
        distances + range. A subset is valid iff every gap (origin->first,
        between charges, last->destination) is <= range. Sorted by (number of
        charges, canonical station order) so the first item is the minimal plan.

        Nothing here is hardcoded: change a distance or the range and the set of
        feasible plans recomputes itself.
        """
        stations = self.stations_along(direction_id)
        pos = self.dist_from_origin(direction_id)
        length = self.total_length(direction_id)
        ids = [s.id for s in stations]
        n = len(ids)
        plans = []
        for mask in range(1 << n):
            chosen = [ids[k] for k in range(n) if mask & (1 << k)]
            points = [0.0] + [pos[c] for c in chosen] + [length]
            ok = all(points[t + 1] - points[t] <= battery_range_km + 1e-9
                     for t in range(len(points) - 1))
            if ok:
                plans.append(tuple(chosen))
        plans.sort(key=lambda pl: (len(pl), tuple(ids.index(c) for c in pl)))
        return plans


# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Weights:
    """The ONE place weights live. Change a weight = one line in the scenario."""
    individual: float = 1.0
    operator: float = 1.0
    overall: float = 1.0

    def for_key(self, weight_key: str) -> float:
        return getattr(self, weight_key)


@dataclass(frozen=True)
class Objective:
    """Which metric each soft rule optimizes — a string the rule dispatches on."""
    individual: str = "total_wait"
    operator: str = "fair_share"
    overall: str = "makespan"


# --------------------------------------------------------------------------- #
# Fleet
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Bus:
    id: str
    operator: str  # free string, no fixed registry
    direction: str  # key into Route.directions
    depart_minute: int  # parsed from "HH:MM"
    priority: int = 0  # future-ready, 0 = no effect today
    allowed_stations: Optional[tuple] = None  # None = any
    min_charges: Optional[int] = None
    attrs: dict = field(default_factory=dict)  # forward-compat per-bus fields


@dataclass
class Scenario:
    id: str
    name: str
    route: Route
    physics: Physics
    weights: Weights
    objective: Objective
    fleet: list  # list[Bus]
    extensions: dict = field(default_factory=dict)  # untyped global forward-compat


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChargeEvent:
    bus_id: str
    station_id: str
    arrive: int
    wait: int  # charge_start - arrive
    charge_start: int
    charge_end: int  # charge_start + charge_minutes
    depart: int  # == charge_end


@dataclass
class BusTimeline:
    bus_id: str
    operator: str
    direction: str
    origin_depart: int
    chosen_stations: list
    events: list  # list[ChargeEvent] in route order
    arrival: int = 0

    @property
    def total_wait(self) -> int:
        return sum(e.wait for e in self.events)

    @property
    def total_journey(self) -> int:
        return self.arrival - self.origin_depart


@dataclass(frozen=True)
class SlotUse:
    bus_id: str
    arrive: int
    wait: int
    charge_start: int
    charge_end: int


@dataclass
class StationOrder:
    station_id: str
    slots: list  # list of list[SlotUse]; len == node.chargers


@dataclass
class ScheduleResult:
    timelines: list  # list[BusTimeline]
    station_orders: list  # list[StationOrder]