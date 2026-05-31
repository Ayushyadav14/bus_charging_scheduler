"""The scheduler engine.

Flow:
1. Pick a feasible charging plan per bus (minimal charges, deterministic),
   validated through the registered HARD rules.
2. Run one global, time-ordered discrete-event simulation of the chargers.
   A bus's arrival at a downstream station therefore reflects every wait it
   absorbed upstream (the schedules are coupled — they cannot be planned
   per-bus).
3. At a charger, whenever a slot is free and buses are waiting, the next bus
   is the one minimising the weighted sum of the SOFT-rule costs; ties break
   on bus id, so output is fully reproducible.

The engine references no station name, no bus/charger count, and no rule name.
It iterates the registries and reads everything else from the scenario.
"""
from __future__ import annotations

import heapq
import itertools

from model import (BusTimeline, ChargeEvent, ScheduleResult, SlotUse,
                   StationOrder)
from rules.base import PickCandidate, PlanCandidate, SchedulingContext
from rules.registry import HARD_RULES, SOFT_RULES

_FREE, _ARRIVE = 0, 1  # event ranks: a freed slot is registered before an
                        # arrival at the same instant, so the arriving bus can
                        # compete for the slot it just freed.


class Engine:
    def __init__(self, hard_rules=None, soft_rules=None):
        # Defaults to the registries — agnostic to which/how many rules exist.
        self.hard_rules = hard_rules if hard_rules is not None else HARD_RULES
        self.soft_rules = soft_rules if soft_rules is not None else SOFT_RULES

    # ---- plan selection ------------------------------------------------- #
    def _plan_for(self, bus, scenario, ctx):
        """Smallest feasible plan that passes every hard rule. Respects optional
        per-bus ``allowed_stations`` / ``min_charges`` if present in the data."""
        plans = scenario.route.feasible_plans(
            bus.direction, scenario.physics.battery_range_km)
        if bus.allowed_stations is not None:
            allowed = set(bus.allowed_stations)
            plans = [p for p in plans if set(p) <= allowed]
        if bus.min_charges is not None:
            plans = [p for p in plans if len(p) >= bus.min_charges]
        for plan in plans:  # already sorted minimal-first
            cand = PlanCandidate(bus=bus, stations=plan)
            if all(r.is_valid(cand, ctx) for r in self.hard_rules):
                return plan
        raise ValueError(f"No hard-rule-valid plan for bus {bus.id}")

    # ---- contention selection ------------------------------------------- #
    def _pick(self, candidates, ctx, weights):
        """Index of the waiting bus to serve next.

        Each soft rule yields a raw value (lower = sooner). We min-max normalise
        each rule across the waiting set so weights are pure trade-off knobs,
        then minimise the weighted sum. Tie-break on bus id for determinism.
        """
        raw = {r: [r.cost(c, ctx) for c in candidates] for r in self.soft_rules}
        norm = {}
        for r, vals in raw.items():
            lo, hi = min(vals), max(vals)
            span = hi - lo
            norm[r] = [(v - lo) / span if span > 1e-12 else 0.0 for v in vals]

        best_i, best_key = None, None
        for i, c in enumerate(candidates):
            wsum = sum(weights.for_key(r.weight_key) * norm[r][i]
                       for r in self.soft_rules)
            key = (round(wsum, 9), c.bus.id)  # weighted cost, then id
            if best_key is None or key < best_key:
                best_key, best_i = key, i
        return best_i

    # ---- main solve ----------------------------------------------------- #
    def solve(self, scenario) -> ScheduleResult:
        route, phys, weights = scenario.route, scenario.physics, scenario.weights
        ctx = SchedulingContext(scenario=scenario)
        fleet = {b.id: b for b in scenario.fleet}

        plans = {b.id: self._plan_for(b, scenario, ctx) for b in scenario.fleet}

        pos = {d: route.dist_from_origin(d) for d in route.directions}
        length = {d: route.total_length(d) for d in route.directions}

        timelines = {
            b.id: BusTimeline(
                bus_id=b.id, operator=b.operator, direction=b.direction,
                origin_depart=b.depart_minute, chosen_stations=list(plans[b.id]),
                events=[], arrival=0)
            for b in scenario.fleet
        }

        station_nodes = {n.id: n for n in route.nodes if n.is_station}
        free_slots = {sid: station_nodes[sid].chargers for sid in station_nodes}
        waiting = {sid: [] for sid in station_nodes}   # [{bus_id, arrive}]
        station_uses = {sid: [] for sid in station_nodes}  # [SlotUse]

        counter = itertools.count()
        evq = []

        def push(time, rank, station=None, bus=None):
            heapq.heappush(evq, (int(time), rank, next(counter), station, bus))

        # Each bus departs full and travels to its first charging station.
        for b in scenario.fleet:
            plan = plans[b.id]
            d = b.direction
            if plan:
                first = plan[0]
                push(b.depart_minute + phys.travel_minutes(pos[d][first]),
                     _ARRIVE, station=first, bus=b.id)
            else:
                timelines[b.id].arrival = b.depart_minute + phys.travel_minutes(length[d])

        def dispatch(sid, now):
            """Start as many waiting buses as there are free slots, each chosen
            by the weighted soft-rule vote."""
            while free_slots[sid] > 0 and waiting[sid]:
                cands = []
                for w in waiting[sid]:
                    b = fleet[w["bus_id"]]
                    remaining = length[b.direction] - pos[b.direction][sid]
                    cands.append(PickCandidate(
                        bus=b, station_id=sid, time=now,
                        wait_so_far=now - w["arrive"], remaining_distance=remaining))
                i = self._pick(cands, ctx, weights)
                w = waiting[sid].pop(i)
                b = fleet[w["bus_id"]]
                d = b.direction

                start = now
                wait = start - w["arrive"]
                end = start + phys.charge_minutes
                free_slots[sid] -= 1

                timelines[b.id].events.append(ChargeEvent(
                    bus_id=b.id, station_id=sid, arrive=w["arrive"], wait=wait,
                    charge_start=start, charge_end=end, depart=end))
                station_uses[sid].append(SlotUse(b.id, w["arrive"], wait, start, end))
                ctx.operator_completions[b.operator] = \
                    ctx.operator_completions.get(b.operator, 0) + 1
                push(end, _FREE, station=sid)

                # schedule the next leg from the station just used
                plan = plans[b.id]
                k = plan.index(sid)
                if k + 1 < len(plan):
                    nxt = plan[k + 1]
                    push(end + phys.travel_minutes(pos[d][nxt] - pos[d][sid]),
                         _ARRIVE, station=nxt, bus=b.id)
                else:
                    timelines[b.id].arrival = \
                        end + phys.travel_minutes(length[d] - pos[d][sid])

        while evq:
            time, rank, _, sid, bus = heapq.heappop(evq)
            if rank == _ARRIVE:
                waiting[sid].append({"bus_id": bus, "arrive": time})
            else:  # a slot freed
                free_slots[sid] += 1
            dispatch(sid, time)

        # ---- assemble outputs ---- #
        order_by_route = [n.id for n in route.nodes if n.is_station]
        station_orders = []
        for sid in order_by_route:
            uses = sorted(station_uses[sid], key=lambda u: (u.charge_start, u.bus_id))
            station_orders.append(StationOrder(station_id=sid, slots=[uses]))

        return ScheduleResult(
            timelines=[timelines[b.id] for b in scenario.fleet],
            station_orders=station_orders,
        )