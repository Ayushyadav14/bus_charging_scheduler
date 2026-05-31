"""Adversarial self-test for the scheduler (Phase 7).

Runs, with NO UI and NO engine changes:
1. the strict validator on all 5 scenarios,
2. a sanity pass on the busiest station for the two contention scenarios,
3. Scenario 4 at operator weight 0.0 vs 2.0 (proves the weight is consumed),
4. four "tomorrow it's different" curveballs as DATA ONLY, with a checksum
   proving zero source files changed.

Usage (from inside the scheduler/ folder):
    python self_test.py
"""
import copy
import glob
import hashlib
from collections import Counter
from dataclasses import replace

import yaml

from loader import load_scenario, build_scenario
from engine import Engine
from validator import verify, verify_determinism
import rules.hard  # noqa: F401 (registers hard-rule plugins)
import rules.soft  # noqa: F401 (registers soft-rule plugins)

ENG = Engine()


def hhmm(m):
    m %= 24 * 60
    return f"{m // 60:02d}:{m % 60:02d}"


def _source_digest():
    """sha256 over every source file, to prove curveballs change no code."""
    h = hashlib.sha256()
    for f in sorted(glob.glob("*.py") + glob.glob("rules/*.py")):
        h.update(open(f, "rb").read())
    return h.hexdigest()


def _max_concurrency(uses):
    pts = sorted([(u.charge_start, 1) for u in uses] + [(u.charge_end, -1) for u in uses],
                 key=lambda x: (x[0], x[1]))
    cur = mx = 0
    for _, d in pts:
        cur += d
        mx = max(mx, cur)
    return mx


# --------------------------------------------------------------------------- #
# Building blocks for the data-only curveballs
# --------------------------------------------------------------------------- #
BASE_NODES = [
    {"id": "BLR",   "name": "Bengaluru", "kind": "endpoint", "full_charge": True},
    {"id": "A",     "name": "A",         "kind": "station",  "chargers": 1},
    {"id": "B",     "name": "B",         "kind": "station",  "chargers": 1},
    {"id": "C",     "name": "C",         "kind": "station",  "chargers": 1},
    {"id": "D",     "name": "D",         "kind": "station",  "chargers": 1},
    {"id": "KOCHI", "name": "Kochi",     "kind": "endpoint", "full_charge": True},
]
BASE_SEG = [
    {"from": "BLR",   "to": "A",     "distance_km": 100},
    {"from": "A",     "to": "B",     "distance_km": 120},
    {"from": "B",     "to": "C",     "distance_km": 100},
    {"from": "C",     "to": "D",     "distance_km": 120},
    {"from": "D",     "to": "KOCHI", "distance_km": 100},
]


def make_world(nodes, segments, fleet, rng=240, charge=25, speed=60):
    return {
        "schema_version": 1,
        "physics": {"battery_range_km": rng, "charge_minutes": charge, "speed_kmph": speed},
        "route": {"id": "r", "nodes": nodes, "segments": segments,
                  "directions": {"BK": {"from": "BLR", "to": "KOCHI"},
                                 "KB": {"from": "KOCHI", "to": "BLR"}}},
        "objective": {"individual": "total_wait", "operator": "fair_share", "overall": "makespan"},
        "weights": {"individual": 1.0, "operator": 1.0, "overall": 1.0},
        "extensions": {},
        "fleet": fleet,
    }


def fleet_of(path):
    return yaml.safe_load(open(path))["fleet"]


def run(raw):
    sc = build_scenario(raw)
    sched = ENG.solve(sc)
    verify(sched, sc)  # raises if any hard rule is violated
    return sc, sched


# --------------------------------------------------------------------------- #
def part1_validate_all():
    print("=" * 68)
    print("PART 1 - strict validator on all 5 scenarios")
    print("=" * 68)
    for path in sorted(glob.glob("scenarios/scenario-*.yaml")):
        sc = load_scenario(path)
        sched = ENG.solve(sc)
        verify(sched, sc)
        verify_determinism(sc, ENG)
        print(f"  {sc.id:11s} {len(sched.timelines):2d} buses : PASS (valid + deterministic)")


def part2_sanity():
    print("\n" + "=" * 68)
    print("PART 2 - busiest station sanity (Scenario 5 and Scenario 2)")
    print("=" * 68)
    for sid in ["scenario-5", "scenario-2"]:
        sc = load_scenario(f"scenarios/{sid}.yaml")
        sched = ENG.solve(sc)
        neg = sum(1 for t in sched.timelines for e in t.events if e.wait < 0)
        busiest = max(sched.station_orders,
                      key=lambda so: sum(u.wait for u in so.slots[0]))
        tot = sum(u.wait for u in busiest.slots[0])
        conc = _max_concurrency(busiest.slots[0])
        print(f"  {sid}: non-negative waits={neg == 0}, busiest={busiest.station_id}, "
              f"max concurrency={conc} (must be <= chargers), total wait at it={tot}m")


def part3_weight_effect():
    print("\n" + "=" * 68)
    print("PART 3 - Scenario 4: operator weight 0.0 vs 2.0 (station A)")
    print("=" * 68)
    sc = load_scenario("scenarios/scenario-4.yaml")
    op = {b.id: b.operator for b in sc.fleet}

    def starts(w):
        s = ENG.solve(replace(sc, weights=replace(sc.weights, operator=w)))
        so = next(x for x in s.station_orders if x.station_id == "A")
        return {u.bus_id: u.charge_start for u in so.slots[0]}

    a0, a2 = starts(0.0), starts(2.0)
    moved = [(b, op[b], a2[b] - a0[b]) for b in a0 if a2[b] != a0[b]]
    for b, o, d in sorted(moved, key=lambda x: x[2]):
        print(f"  {b} ({o}) shifts {d:+d} min when operator weight 0.0 -> 2.0")
    if not moved:
        print("  NONE -> soft rules ignore the weight (bug)")


def part4_curveballs():
    print("\n" + "=" * 68)
    print("PART 4 - data-only curveballs (zero engine changes)")
    print("=" * 68)
    before = _source_digest()
    f5 = fleet_of("scenarios/scenario-5.yaml")
    f1 = fleet_of("scenarios/scenario-1.yaml")

    # (a) capacity on the congested station A
    n2 = copy.deepcopy(BASE_NODES)
    next(n for n in n2 if n["id"] == "A")["chargers"] = 2
    _, base = run(make_world(BASE_NODES, BASE_SEG, f5))
    _, cap2 = run(make_world(n2,         BASE_SEG, f5))
    w1 = sum(u.wait for so in base.station_orders if so.station_id == "A" for u in so.slots[0])
    w2 = sum(u.wait for so in cap2.station_orders if so.station_id == "A" for u in so.slots[0])
    print(f"  (a) chargers at A 1 -> 2: total wait at A {w1}m -> {w2}m [accepted, valid]")

    # (b) add station E between C and D
    nodesE = copy.deepcopy(BASE_NODES)
    nodesE.insert(4, {"id": "E", "name": "E", "kind": "station", "chargers": 1})
    segE = [{"from": "BLR",   "to": "A",     "distance_km": 100},
            {"from": "A",     "to": "B",     "distance_km": 120},
            {"from": "B",     "to": "C",     "distance_km": 100},
            {"from": "C",     "to": "E",     "distance_km":  60},
            {"from": "E",     "to": "D",     "distance_km":  60},
            {"from": "D",     "to": "KOCHI", "distance_km": 100}]
    scE, _ = run(make_world(nodesE, segE, f1))
    eplans = [p for p in scE.route.feasible_plans("BK", 240) if "E" in p][:3]
    print(f"  (b) add station E: BK stations {[n.id for n in scE.route.stations_along('BK')]}, "
          f"E now usable e.g. {eplans} [accepted, valid]")

    # (c) range 200 -> min charges recompute
    sc200, s200 = run(make_world(BASE_NODES, BASE_SEG, f1, rng=200))
    mc = len(sc200.route.feasible_plans("BK", 200)[0])
    ch = {len(t.events) for t in s200.timelines if t.direction == "BK"}
    print(f"  (c) range 240 -> 200: BK min-charges 2 -> {mc}, every BK bus now charges {ch} [recomputed from data]")

    # (d) 4th operator
    f1b = copy.deepcopy(f1)
    for b in f1b:
        if b["id"] in ("bus-BK-03", "bus-BK-06", "bus-KB-02", "bus-KB-08"):
            b["operator"] = "zingbus"
    scd, _ = run(make_world(BASE_NODES, BASE_SEG, f1b))
    print(f"  (d) 4th operator: counts {dict(Counter(b.operator for b in scd.fleet))} [accepted, valid]")

    after = _source_digest()
    print(f"\n  ZERO CODE EDITS: source sha256 unchanged ? {before == after}")


def main():
    part1_validate_all()
    part2_sanity()
    part3_weight_effect()
    part4_curveballs()
    print("\nAll adversarial checks complete.")


if __name__ == "__main__":
    main()