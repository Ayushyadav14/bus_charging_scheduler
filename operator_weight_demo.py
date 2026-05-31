"""Show how raising the operator weight changes the charger order in Scenario 4.

Usage (from inside the scheduler/ folder):
    python operator_weight_demo.py
"""
from dataclasses import replace

from loader import load_scenario
from engine import Engine
import rules.hard  # noqa: F401
import rules.soft  # noqa: F401


def hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def order_at(engine, scenario, station_id):
    result = engine.solve(scenario)
    so = next(s for s in result.station_orders if s.station_id == station_id)
    uses = sorted(so.slots[0], key=lambda u: (u.charge_start, u.bus_id))
    op = {b.id: b.operator for b in scenario.fleet}
    return [(u.bus_id, op[u.bus_id], hhmm(u.charge_start)) for u in uses]


def main() -> None:
    engine = Engine()
    sc = load_scenario("scenarios/scenario-4.yaml")
    sc_low = replace(sc, weights=replace(sc.weights, operator=1.0))
    sc_high = replace(sc, weights=replace(sc.weights, operator=2.0))

    for station in ["A", "C"]:
        low = order_at(engine, sc_low, station)
        high = order_at(engine, sc_high, station)
        print(f"\n=== Station {station}: charging order ===")
        print(f"{'pos':>3} {'operator = 1.0':28s} {'operator = 2.0':28s} flip?")
        for i, (a, b) in enumerate(zip(low, high), 1):
            flip = " <-- DIFFERENT" if a[0] != b[0] else ""
            la = f"{a[0]} {a[1]} @{a[2]}"
            lb = f"{b[0]} {b[1]} @{b[2]}"
            print(f"{i:>3} {la:28s} {lb:28s}{flip}")


if __name__ == "__main__":
    main()