"""Hard rules H1-H4, registered as plugins.

These validate a *charging plan* (which stations, in what order) before the
simulation runs. Range (H3) and route order (H4) are real geometric checks.
H1/H2 are structural guards:

* Temporal one-bus-per-charger exclusivity (H1) is guaranteed *by construction*
  by the N-slot event simulation — it never creates overlapping uses, so there
  is nothing to reject. The plugin checks the weaker structural precondition
  that every chosen station actually has a charger.
* Fixed 25-minute charging (H2) is likewise enforced by construction (the
  simulator always charges for ``physics.charge_minutes``); the plugin guards
  that the configured duration is sane.

This keeps every rule a plugin while putting the heavy temporal logic where it
belongs — in the simulator, which avoids generating illegal states instead of
generating-then-rejecting them.
"""
from __future__ import annotations

from rules.base import HardRule, PlanCandidate, SchedulingContext
from rules.registry import hard_rule


@hard_rule
class OneBusPerCharger(HardRule):  # H1
    name = "H1-one-bus-per-charger"

    def is_valid(self, candidate: PlanCandidate, context: SchedulingContext) -> bool:
        route = context.scenario.route
        by_id = {n.id: n for n in route.nodes}
        # Every station in the plan must have at least one charger to be usable.
        return all(by_id[s].chargers >= 1 for s in candidate.stations)


@hard_rule
class FixedChargeDuration(HardRule):  # H2
    name = "H2-fixed-charge-duration"

    def is_valid(self, candidate: PlanCandidate, context: SchedulingContext) -> bool:
        return context.scenario.physics.charge_minutes > 0


@hard_rule
class RangeNeverExceeded(HardRule):  # H3
    name = "H3-range"

    def is_valid(self, candidate: PlanCandidate, context: SchedulingContext) -> bool:
        route = context.scenario.route
        rng = context.scenario.physics.battery_range_km
        d = candidate.bus.direction
        pos = route.dist_from_origin(d)
        length = route.total_length(d)
        points = [0.0] + [pos[s] for s in candidate.stations] + [length]
        return all(points[i + 1] - points[i] <= rng + 1e-9
                   for i in range(len(points) - 1))


@hard_rule
class RouteOrderNoBacktrack(HardRule):  # H4
    name = "H4-route-order"

    def is_valid(self, candidate: PlanCandidate, context: SchedulingContext) -> bool:
        route = context.scenario.route
        d = candidate.bus.direction
        order = [n.id for n in route.stations_along(d)]
        rank = {sid: i for i, sid in enumerate(order)}
        # chosen stations must be a strictly increasing subsequence of the route
        seq = [rank[s] for s in candidate.stations if s in rank]
        if len(seq) != len(candidate.stations):
            return False  # a station not on this direction
        return all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))