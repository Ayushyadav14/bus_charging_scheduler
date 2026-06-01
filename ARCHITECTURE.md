# Architecture — Bus Charging Scheduler

Approach, data model, anticipated changes, and honest status.

---

## 1. Scheduling approach, and why it fits

**Chosen:** a deterministic discrete-event simulation with pluggable rules. Hard rules are
constraint-checker plugins; soft rules are weighted cost-function plugins. Plans are selected
per bus from feasible options, and contention at a charger is resolved by minimising the
weighted sum of the soft-rule costs.

**Alternative considered:** a constraint-programming model (OR-Tools CP-SAT) — hard rules
as constraints, soft rules as objective terms, solved to optimality.

### Why the simulation approach wins for this problem

**The plugin requirement is native to it.** The brief demands rules that are registered, where
"adding a rule = adding a class, the engine never changes." That is exactly the shape of a
simulation that loops over a rule registry. In a CP model, a new rule means new decision
variables and constraints threaded into one monolithic model — the opposite of a clean plugin
boundary.

**It is explainable.** Because the engine commits buses in time order, every decision can be
narrated ("bus B charged before C because its weighted cost was lower — here are the
numbers"). A solver returns an optimal assignment but not a *why*, which is weak when you
must defend each decision live.

**It is deterministic by construction** (integer-minute clock, total-order tie-break on bus id).
CP-SAT needs single-threaded mode and a fixed seed to be reproducible, which is easy to
get wrong.

**It ships fast and stays small.** Plain Python, no solver modelling.

**The honest trade:** greedy contention resolution is not provably globally optimal. For these
sizes that is irrelevant, and "a fast, tunable, fully explainable heuristic" is the defensible
story. Crucially, the selection strategy sits behind the rule interfaces, so a local-search pass —
or even a CP-SAT backend — could replace it later without touching a single rule. We ship
the simulation now and keep the optimiser option open.

---

## 2. Data-structure design (and why each field exists)

A scenario is a data file that fully describes the world. It is split into a shared `world.yaml`
(route, physics, default weights) and per-scenario files that `extends` it and override only
the fleet. The loader also accepts fully self-contained files. The typed in-memory model:

**`Physics`** — `battery_range_km`, `charge_minutes`, `speed_kmph`. Every physical constant
lives here so none is a literal in the engine. `travel_minutes()` derives travel time from
`speed_kmph`, so changing speed is a data edit.

**`Node`** — `id`, `name`, `kind` (`endpoint` | `station`), `chargers` (capacity N, default 1),
`full_charge`, and a free `attrs` dict. Endpoints and stations share one type because the
route is just an ordered list of points; `kind` flags which are scheduled. `chargers` is
modelled as N from day one so "1" is never hardcoded. `attrs` preserves any unknown field
for forward compatibility.

**`Direction`** — `id`, `from_id`, `to_id`. A direction is a (from → to) pair over the canonical
node list, so BK is a forward walk and KB the reverse walk of the same list. There is no
`if direction == "KB"` anywhere.

**`Route`** — `nodes` (canonical forward order), `seg_km` (symmetric distance lookup keyed by
the unordered station pair), `directions`. Its helpers (`stations_along`, `dist_from_origin`,
`total_length`, `feasible_plans`) derive everything from data — including the set of valid
charging-station subsets, which is computed from distances + range and never hardcoded.

**`Weights`** — `individual`, `operator`, `overall`, plus `for_key(name)`. The single home for
the three tunable weights; the engine multiplies a soft rule's cost by
`weights.for_key(rule.weight_key)` and nowhere else.

**`Objective`** — which metric each soft rule optimises, as strings, so the metric choice is
data too.

**`Bus`** — `id`, `operator` (a free string, no registry), `direction` (a key into
`Route.directions`), `depart_minute`, and forward-ready optional fields `priority`,
`allowed_stations`, `min_charges`, plus `attrs`. The optional fields exist so common future
asks (priority buses, per-bus station limits) need no schema change; `attrs` catches
anything else.

**`Scenario`** — ties the above together and carries an untyped `extensions` dict: a
forward-compat bag for future global data (price curves, shift windows) that the loader
preserves and future rules read.

**Outputs** — `ChargeEvent` (arrive/wait/charge_start/charge_end/depart per stop),
`BusTimeline` (events + arrival + derived totals), `SlotUse` and `StationOrder` (the
per-station order). These mirror exactly the two views the UI must render, so the UI is a
thin projection of the result.

**The thread tying it together:** the loader is strict on known fields and permissive on unknown
ones (extras land in `attrs` / `extensions`), and everything physical is derived, never literal.

---

## 3. Anticipated future changes

The engine exposes two plugin seams today — plan-validity (`HardRule` on a proposed
charging plan) and contention-order (`SoftRule` on a waiting bus) — and reads the world
entirely from data. The table below states, for each anticipated change, the mechanism that
absorbs it.

| Change | Absorbed by | Engine code touched? |
|---|---|---|
| More buses | `fleet` entries (data) | No |
| More stations | `route.nodes` + `segments` (data); feasible plans recompute | No |
| More operators | the `operator` string (data); no operator registry exists | No |
| Multiple chargers per station | `chargers: N` on the node (data) | No |
| Changed segment distances | `segments[].distance_km` (data) | No |
| Changed speed | `physics.speed_kmph` (data); travel time derived | No |
| Global range change | `physics.battery_range_km` (data); min-charges recompute | No |
| Global charge-time change | `physics.charge_minutes` (data) | No |
| Priority buses | new `SoftRule` reading `bus.priority` (plugin) | No |
| Time-of-day electricity pricing | new `SoftRule` reading a price curve from `extensions` (plugin) | No |
| Per-operator SLAs | new `SoftRule` reading SLA targets from `extensions` (plugin) | No |
| Per-bus station limits | `allowed_stations` field, or a `HardRule` reading `attrs` (plugin) | No |
| Tighter per-bus range | new `HardRule` re-checking legs against `bus.attrs['range_km']` (plugin) | No |
| Larger per-bus range | read the per-bus range in plan generation | One line in plan selection |
| Per-station charge time | `node.attrs['charge_minutes']` (data) | One line at dispatch |
| Driver-shift windows (hard) | new `HardRule` via the availability seam (below) | Add the seam once (~3 lines) |
| Charger downtime / maintenance (hard) | new `HardRule` via the availability seam | Add the seam once |
| Multiple routes sharing stations | `routes` list + `bus.route_id`; chargers are keyed by station id so sharing is automatic | One lookup (resolve each bus's route) |

Most of the list is pure data or a pure plugin with the engine untouched (several proven live
in the self-test: capacity-2 cut wait 765→180 min, adding a 5th station, range 240→200
recomputing minimum charges 2→4, a 4th operator).

### The one anticipated seam — availability

Temporal hard constraints (maintenance windows, driver shifts, charger downtime) cannot be
expressed as a plan-validity check, because a plan carries no timing. They belong at the
moment a charge would start. The design anticipates a third seam: a predicate consulted in
the dispatcher before a charge begins —

```python
def is_available(station_id, start, end, bus, context) -> bool: ...
```

— with temporal `HardRule`s registered into an availability list. This is a small, well-defined
addition (the dispatcher already decides when a charge starts; the seam just gates it). It is
listed honestly in "what's next" rather than claimed as already present.

---

## 4. Worked example — change a weight

Weights live only in the scenario. To soften operator fairness in Scenario 4, change one
value in `scenarios/scenario-4.yaml`:

```yaml
# before
weights: { individual: 1.0, operator: 2.0, overall: 1.0 }

# after
weights: { individual: 1.0, operator: 0.5, overall: 1.0 }
```

No code changes. The engine reads the value through `weights.for_key(rule.weight_key)`,
so every soft rule honours it. The sidebar sliders do the same at runtime by building a
`Weights` object and re-solving.

---

## 5. Worked example — add a rule (no engine edits)

### Soft rule — priority buses charge first

```python
# rules/soft.py
@soft_rule
class PriorityFirst(SoftRule):
    name = "S4-priority"
    weight_key = "overall"  # reuses an existing weight; Weights unchanged

    def cost(self, candidate, context):
        return -float(getattr(candidate.bus, "priority", 0))
```

Data: `{ id: bus-BK-10, ..., priority: 9 }`

Verified: bus-BK-10 moves from position 10 to position 4 at station A. The engine was not
edited.

### Hard rule — forbid certain stations for a bus

```python
# rules/hard.py
@hard_rule
class ForbiddenStations(HardRule):
    name = "H5-forbidden-stations"

    def is_valid(self, candidate, context):
        forbid = set(candidate.bus.attrs.get("forbid", []))
        return not (set(candidate.stations) & forbid)
```

Data: `{ id: bus-BK-01, ..., forbid: [C] }`

Verified: that bus's plan changes from `{A, C}` to the next feasible plan `{B, D}`. The engine
was not edited.

Both decorate a class, land in the registry the engine already iterates, and run immediately.

---

## 6. What's done / not done / next

### Done

- The full engine: data-derived feasible plans, a global time-ordered charger simulation with
  capacity-N, weight-driven contention resolution, and the two output views.
- Hard rules H1–H4 and soft rules S1–S3 as registered plugins.
- The loader with `extends` and deep-merge.
- A strict validator (every hard rule re-derived from output, plus determinism) — all five
  scenarios pass and are deterministic.
- An adversarial self-test including four data-only curveballs (chargers-per-station, a 5th
  station, a tighter range, a 4th operator), all with zero code edits confirmed by checksum.
- The Streamlit UI with the required three views and live weight sliders.
- The two worked rule extensions above are tested.

### Not done / known limitations

- **Plan selection is fixed-minimal, not congestion-aware:** a bus takes the smallest
  canonical feasible plan and never charges extra to dodge a queue, even though the data
  model permits it. Consequence: under the default plans the two directions happen not to
  share a charger, so cross-direction collisions are not exercised (the engine fully supports
  them — the capacity sweep is per station id — they are simply not triggered).
- **Greedy, not globally optimal** (by design; see §1).
- **The availability seam** for temporal hard rules is designed but not implemented (§3), so
  maintenance windows / driver shifts are not yet enforceable.
- **S3 (overall) is largely inert** in these single-route scenarios because buses at one station
  share the same remaining distance; it bites under shared stations / multiple routes.

### Next, in order of value

1. Add the availability seam, then ship maintenance-window and driver-shift hard rules as
   plugins.
2. Make plan selection congestion-aware (a second strategy behind the same rule
   interfaces), which would also exercise cross-direction sharing.
3. Optionally add a local-search pass over the greedy result for tighter total wait, still behind
   the rule interfaces.