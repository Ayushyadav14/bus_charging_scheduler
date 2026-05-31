"""Invariant validator for a produced schedule.

``verify(schedule, scenario)`` re-derives every hard rule directly from the
output and the scenario and raises ``InvariantViolation`` with a precise message
on the first failure. It trusts nothing the engine claims; it checks.

``verify_determinism(scenario)`` solves the same scenario several times and
asserts identical output.

Run directly to validate all bundled scenarios:
    python validator.py
"""
from __future__ import annotations

from collections import defaultdict

from loader import load_scenario
import rules.hard  # noqa: F401 (registration not required here, kept for parity)
import rules.soft  # noqa: F401


class InvariantViolation(AssertionError):
    """Raised when a produced schedule breaks a hard rule."""


def _require(condition, message):
    if not condition:
        raise InvariantViolation(message)


def _min_charges(scenario, direction):
    """Minimum number of charges for a direction, computed from data (not hardcoded)."""
    plans = scenario.route.feasible_plans(
        direction, scenario.physics.battery_range_km)
    _require(plans, f"no feasible plan exists for direction {direction}")
    return len(plans[0])


def verify(schedule, scenario) -> bool:
    """Assert ALL hard rules on a produced schedule. Returns True or raises."""
    route = scenario.route
    phys = scenario.physics
    charge_time = phys.charge_minutes
    rng = phys.battery_range_km
    stations = {n.id: n for n in route.nodes if n.is_station}

    ids = [t.bus_id for t in schedule.timelines]
    _require(len(ids) == len(scenario.fleet) and len(set(ids)) == len(ids),
             "fleet mismatch: timelines do not match the scenario's buses one-to-one")

    pos = {d: route.dist_from_origin(d) for d in route.directions}
    length = {d: route.total_length(d) for d in route.directions}

    # ----------------------------------------------------------------- #
    # Per-bus checks
    # ----------------------------------------------------------------- #
    for t in schedule.timelines:
        d = t.direction
        _require(d in route.directions, f"{t.bus_id}: unknown direction '{d}'")
        order = [n.id for n in route.stations_along(d)]
        rank = {sid: i for i, sid in enumerate(order)}

        prev_rank = -1
        for e in t.events:
            # station is real and usable (H1 structural precondition)
            _require(e.station_id in stations,
                     f"{t.bus_id}: charges at non-station '{e.station_id}'")
            _require(stations[e.station_id].chargers >= 1,
                     f"{t.bus_id}: station '{e.station_id}' has no charger")
            # timing identities
            _require(e.wait >= 0,
                     f"{t.bus_id} @ {e.station_id}: negative wait {e.wait}")
            _require(e.charge_start == e.arrive + e.wait,
                     f"{t.bus_id} @ {e.station_id}: charge_start {e.charge_start} "
                     f"!= arrive {e.arrive} + wait {e.wait}")
            _require(e.charge_end - e.charge_start == charge_time,
                     f"{t.bus_id} @ {e.station_id}: charge duration "
                     f"{e.charge_end - e.charge_start} != {charge_time}")
            _require(e.depart == e.charge_end,
                     f"{t.bus_id} @ {e.station_id}: depart {e.depart} "
                     f"!= charge_end {e.charge_end}")
            # route order, no backtracking (H4)
            _require(e.station_id in rank,
                     f"{t.bus_id}: station '{e.station_id}' not on direction {d}")
            r = rank[e.station_id]
            _require(r > prev_rank,
                     f"{t.bus_id}: backtrack or repeat at '{e.station_id}' "
                     f"(route order violated)")
            prev_rank = r

        # minimum charges for this direction/range (computed)
        need = _min_charges(scenario, d)
        _require(len(t.events) >= need,
                 f"{t.bus_id}: charges {len(t.events)} < minimum {need} for {d}")

        # range on every leg (H3)
        stns = [e.station_id for e in t.events]
        points = [0.0] + [pos[d][s] for s in stns] + [length[d]]
        for i in range(len(points) - 1):
            gap = points[i + 1] - points[i]
            _require(gap <= rng + 1e-9,
                     f"{t.bus_id}: leg {i} = {gap:.0f} km exceeds range {rng:.0f} km")

        # temporal continuity: travel time ties the legs together
        if stns:
            first = t.origin_depart + phys.travel_minutes(pos[d][stns[0]])
            _require(t.events[0].arrive == first,
                     f"{t.bus_id}: first arrive {t.events[0].arrive} != expected {first}")
            for a, b in zip(t.events, t.events[1:]):
                exp = a.depart + phys.travel_minutes(pos[d][b.station_id] - pos[d][a.station_id])
                _require(b.arrive == exp,
                         f"{t.bus_id}: arrive at {b.station_id} {b.arrive} != expected {exp}")
            last = t.events[-1]
            exp_arr = last.depart + phys.travel_minutes(length[d] - pos[d][last.station_id])
            _require(t.arrival == exp_arr,
                     f"{t.bus_id}: final arrival {t.arrival} != expected {exp_arr}")
        else:
            exp_arr = t.origin_depart + phys.travel_minutes(length[d])
            _require(t.arrival == exp_arr,
                     f"{t.bus_id}: no-charge arrival {t.arrival} != expected {exp_arr}")

    # ----------------------------------------------------------------- #
    # Per-station capacity (H1 temporal) via a sweep line
    # ----------------------------------------------------------------- #
    intervals = defaultdict(list)
    for t in schedule.timelines:
        for e in t.events:
            intervals[e.station_id].append((e.charge_start, e.charge_end))

    for sid, ivs in intervals.items():
        cap = stations[sid].chargers
        # half-open [start, end): at equal time, ends (-1) before starts (+1)
        deltas = sorted([(s, +1) for s, _ in ivs] + [(e, -1) for _, e in ivs],
                        key=lambda x: (x[0], x[1]))
        cur = mx = 0
        for _, delta in deltas:
            cur += delta
            mx = max(mx, cur)
        _require(mx <= cap,
                 f"station {sid}: {mx} buses charging at once exceeds capacity {cap}")

    # ----------------------------------------------------------------- #
    # The two output views must agree
    # ----------------------------------------------------------------- #
    from_orders = defaultdict(set)
    for so in schedule.station_orders:
        for u in (so.slots[0] if so.slots else []):
            from_orders[so.station_id].add((u.bus_id, u.charge_start, u.charge_end))
    from_timelines = defaultdict(set)
    for t in schedule.timelines:
        for e in t.events:
            from_timelines[e.station_id].add((e.bus_id, e.charge_start, e.charge_end))
    for sid in set(from_orders) | set(from_timelines):
        _require(from_orders[sid] == from_timelines[sid],
                 f"station {sid}: per-station view disagrees with per-bus timelines")

    return True


def _signature(schedule):
    rows = tuple(
        (t.bus_id, t.arrival, t.total_wait,
         tuple((e.station_id, e.arrive, e.wait, e.charge_start, e.charge_end)
               for e in t.events))
        for t in sorted(schedule.timelines, key=lambda x: x.bus_id)
    )
    orders = tuple(
        (s.station_id, tuple((u.bus_id, u.charge_start)
                             for u in (s.slots[0] if s.slots else [])))
        for s in sorted(schedule.station_orders, key=lambda x: x.station_id)
    )
    return rows, orders


def verify_determinism(scenario, engine=None, runs=3) -> bool:
    """Solve the same scenario+weights several times; output must be identical."""
    from engine import Engine
    eng = engine or Engine()
    sigs = [_signature(eng.solve(scenario)) for _ in range(runs)]
    _require(all(s == sigs[0] for s in sigs),
             "non-deterministic: repeated solve() produced different output")
    return True


def main() -> None:
    import glob
    from engine import Engine
    eng = Engine()
    for path in sorted(glob.glob("scenarios/scenario-*.yaml")):
        sc = load_scenario(path)
        schedule = eng.solve(sc)
        verify(schedule, sc)
        verify_determinism(sc, eng)
        print(f"{sc.id:11s} OK ({len(schedule.timelines)} buses) "
              f"- all hard invariants hold, output deterministic")
    print("\nAll scenarios pass every hard invariant.")


if __name__ == "__main__":
    main()