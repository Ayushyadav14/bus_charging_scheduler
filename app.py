"""Streamlit UI for the bus charging scheduler.

One file, one process. It reads scenarios from scenarios/, runs the existing
engine, and shows three views: the scenario input, the per-bus timetable, and
the per-station charging order. Weight sliders let a reviewer retune live; they
write into the same Weights config the engine reads.

Run locally: streamlit run app.py
Deploy: Streamlit Community Cloud, main file = app.py
"""
import glob
import os
from dataclasses import replace

import pandas as pd
import streamlit as st

from loader import load_scenario
from engine import Engine
from model import Weights
from validator import verify
import rules.hard  # noqa: F401 (import registers the hard-rule plugins)
import rules.soft  # noqa: F401 (import registers the soft-rule plugins)

HERE = os.path.dirname(os.path.abspath(__file__))
SCEN_DIR = os.path.join(HERE, "scenarios")


# --------------------------------------------------------------------------- #
# Small pure helpers (no Streamlit) so the UI stays thin
# --------------------------------------------------------------------------- #
def hhmm(minutes: int) -> str:
    day, rem = divmod(int(minutes), 24 * 60)
    text = f"{rem // 60:02d}:{rem % 60:02d}"
    return text + (f" (+{day}d)" if day else "")


def route_string(route) -> str:
    parts = [route.nodes[0].name]
    for a, b in zip(route.nodes, route.nodes[1:]):
        km = route.seg_km[frozenset((a.id, b.id))]
        parts.append(f"--{km:.0f}km--> {b.name}")
    return " ".join(parts)


def chargers_summary(route) -> str:
    return ", ".join(f"{n.id}:{n.chargers}" for n in route.nodes if n.is_station)


def fleet_rows(scenario):
    rows = [{"Bus": b.id, "Operator": b.operator, "Direction": b.direction,
             "Departure": hhmm(b.depart_minute)} for b in scenario.fleet]
    return pd.DataFrame(rows)


def bus_event_rows(timeline):
    rows = [{"Station": e.station_id, "Arrive": hhmm(e.arrive),
             "Wait (min)": e.wait, "Charge start": hhmm(e.charge_start),
             "Charge end": hhmm(e.charge_end)} for e in timeline.events]
    return pd.DataFrame(rows)


def station_rows(station_order, op_by_bus):
    uses = sorted(station_order.slots[0], key=lambda u: (u.charge_start, u.bus_id))
    rows = []
    for i, u in enumerate(uses, 1):
        rows.append({"#": i, "Bus": u.bus_id, "Operator": op_by_bus[u.bus_id],
                     "Arrive": hhmm(u.arrive), "Charge start": hhmm(u.charge_start),
                     "Charge end": hhmm(u.charge_end),
                     "Waited": f"yes ({u.wait}m)" if u.wait > 0 else "no"})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Bus Charging Scheduler", layout="wide")
st.title("Bus Charging Scheduler")

# --- scenario dropdown at the very top ---
files = sorted(glob.glob(os.path.join(SCEN_DIR, "scenario-*.yaml")))
labels = {}
for f in files:
    sc = load_scenario(f)
    labels[f"{sc.id} — {sc.name}"] = f
choice = st.selectbox("Scenario", list(labels.keys()))
scenario = load_scenario(labels[choice])

# --- weight sliders (default to the scenario's weights; write into one Weights) ---
st.sidebar.header("Weights")
st.sidebar.caption(
    f"Scenario default: individual {scenario.weights.individual} / "
    f"operator {scenario.weights.operator} / overall {scenario.weights.overall}")
wi = st.sidebar.slider("Individual (S1)", 0.0, 3.0,
                        float(scenario.weights.individual), 0.1, key=f"wi_{scenario.id}")
wo = st.sidebar.slider("Operator (S2)", 0.0, 3.0,
                        float(scenario.weights.operator), 0.1, key=f"wo_{scenario.id}")
wv = st.sidebar.slider("Overall (S3)", 0.0, 3.0,
                        float(scenario.weights.overall), 0.1, key=f"wv_{scenario.id}")

active = replace(scenario, weights=Weights(individual=wi, operator=wo, overall=wv))
retuned = (wi, wo, wv) != (scenario.weights.individual,
                            scenario.weights.operator,
                            scenario.weights.overall)
if retuned:
    st.sidebar.info("Weights retuned live — schedule recomputed below.")

# --- solve + validate ---
result = Engine().solve(active)
op_by_bus = {b.id: b.operator for b in active.fleet}
try:
    verify(result, active)
    st.caption("Schedule validated: all hard rules hold, output is deterministic.")
except Exception as exc:  # pragma: no cover
    st.error(f"Validation failed: {exc}")

tab_input, tab_bus, tab_station = st.tabs(
    ["Scenario input", "Per-bus timetable", "Per-station view"])

# --- Tab 1: input ---
with tab_input:
    st.subheader("World settings")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Battery range", f"{active.physics.battery_range_km:.0f} km")
    c2.metric("Charge time", f"{active.physics.charge_minutes} min")
    c3.metric("Speed", f"{active.physics.speed_kmph:.0f} km/h")
    c4.metric("Weights (i/o/v)", f"{wi}/{wo}/{wv}")
    st.markdown(f"**Route:** {route_string(active.route)}")
    st.markdown(f"**Chargers per station:** {chargers_summary(active.route)}")

    st.subheader("Departure schedule (raw input)")
    st.dataframe(fleet_rows(active), hide_index=True, use_container_width=True)

# --- Tab 2: per-bus timetable ---
with tab_bus:
    st.caption("Each bus's full timeline. The expander title carries the summary; "
               "open it for the per-station charge windows.")
    for direction_id in active.route.directions:
        buses = [t for t in result.timelines if t.direction == direction_id]
        if not buses:
            continue
        st.subheader(f"Direction {direction_id}")
        for t in buses:
            title = (f"{t.bus_id} · {t.operator} · depart {hhmm(t.origin_depart)} "
                     f"· charges at {', '.join(t.chosen_stations)} "
                     f"· arrive {hhmm(t.arrival)} · total wait {t.total_wait} min")
            with st.expander(title):
                st.dataframe(bus_event_rows(t), hide_index=True, use_container_width=True)
                st.markdown(f"**Final arrival:** {hhmm(t.arrival)} · "
                            f"**total journey:** {t.total_journey} min")

# --- Tab 3: per-station view ---
with tab_station:
    st.caption("For each station, the order buses used the charger, with windows "
               "and who had to wait.")
    for so in result.station_orders:
        node = next(n for n in active.route.nodes if n.id == so.station_id)
        st.subheader(f"Station {so.station_id} — {node.chargers} charger(s)")
        df = station_rows(so, op_by_bus)
        if df.empty:
            st.write("No buses charged here.")
        else:
            st.dataframe(df, hide_index=True, use_container_width=True)