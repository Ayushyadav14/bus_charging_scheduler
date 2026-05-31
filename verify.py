"""Run every scenario and verify the hard rules hold.

Usage (from inside the scheduler/ folder):
    python verify.py
"""
import glob

from loader import load_scenario
from engine import Engine
import rules.hard  # noqa: F401 (import registers the hard-rule plugins)
import rules.soft  # noqa: F401 (import registers the soft-rule plugins)


def hhmm(minutes: int) -> str:
    day, minutes = divmod(minutes, 24 * 60)
    text = f"{minutes // 60:02d}:{minutes % 60:02d}"
    return text + (f"+{day}d" if day else "")


def main() -> None:
    engine = Engine()
    for path in sorted(glob.glob("scenarios/scenario-*.yaml")):
        sc = load_scenario(path)
        result = engine.solve(sc)

        rng = sc.physics.battery_range_km
        pos = {d: sc.route.dist_from_origin(d) for d in sc.route.directions}
        length = {d: sc.route.total_length(d) for d in sc.route.directions}

        range_viol = 0
        for t in result.timelines:
            pts = [0.0] + [pos[t.direction][e.station_id] for e in t.events] + [length[t.direction]]
            gaps = [pts[i + 1] - pts[i] for i in range(len(pts) - 1)]
            if any(g > rng + 1e-9 for g in gaps):
                range_viol += 1
            if len(t.events) < 2:  # BK and KB both need >= 2 charges
                range_viol += 1

        overlap = 0
        for so in result.station_orders:
            uses = sorted(so.slots[0], key=lambda u: u.charge_start)
            for a, b in zip(uses, uses[1:]):
                if b.charge_start < a.charge_end:
                    overlap += 1

        waits = [t.total_wait for t in result.timelines]
        arrivals = [t.arrival for t in result.timelines]
        print(f"{sc.id:11s} buses={len(result.timelines):2d} "
              f"range_viol={range_viol} charger_overlap={overlap} "
              f"max_wait={max(waits):3d} tot_wait={sum(waits):4d} "
              f"last_arrival={hhmm(max(arrivals))}")


if __name__ == "__main__":
    main()