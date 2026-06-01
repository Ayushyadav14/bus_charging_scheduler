# Bus Charging Scheduler

Schedules charging for electric buses running both directions on a fixed route
(**Bengaluru → A → B → C → D → Kochi**) that share four single-charger stations.
It decides each bus's charging plan and the order buses use each charger, and renders
the per-bus timetable and per-station order.

**Built to change by editing data or rules — never the engine.**

---

## Links

| | |
|---|---|
| 🌐 **Live app** | https://buschargingscheduler-2tskvjzrwufhnawhmnhfua.streamlit.app/ |
| 📦 **Repository** | https://github.com/Ayushyadav14/bus_charging_scheduler |

---

## At a glance

- Reads any scenario from a YAML data file; ships **five scenarios**.
- **Hard rules** (range, route order, one-bus-per-charger, fixed charge time) and **soft rules**
  (individual wait, operator fairness, overall time) are **registered plugins** — adding a rule
  never edits the engine.
- Three tunable **weights** live in one place and can be retuned live in the UI.
- **Deterministic:** same scenario + weights → identical output every time.
- Stack: **Python 3.10+ · Streamlit · PyYAML**. One repo, one process. No DB, no auth.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| UI | Streamlit 1.40.2 |
| Data frames | pandas 2.2.3 |
| Config / scenarios | PyYAML 6.0.2 |
| Scheduling engine | Custom discrete-event simulation (pure Python) |
| Rule system | Plugin registry (`@hard_rule` / `@soft_rule` decorators) |
| Validation | Custom invariant checker (`validator.py`) |
| Deployment | Streamlit Community Cloud |

---

## Project layout

```
bus-charging-scheduler/
├── app.py                   # Streamlit UI (one file)
├── requirements.txt         # Pinned deps for Streamlit Community Cloud
├── model.py                 # Typed data model + output types
├── loader.py                # YAML loader (extends + deep-merge)
├── engine.py                # Scheduler (plan selection + event simulation)
├── validator.py             # Strict hard-rule + determinism checker
├── verify.py                # Quick smoke test
├── self_test.py             # Adversarial self-test + data-only curveballs
├── operator_weight_demo.py  # Shows weight effect on Scenario 4
├── rules/
│   ├── __init__.py
│   ├── base.py              # HardRule / SoftRule ABCs + candidate types
│   ├── registry.py          # @hard_rule / @soft_rule decorators
│   ├── hard.py              # H1–H4 hard constraints
│   └── soft.py              # S1–S3 soft preferences
└── scenarios/
    ├── world.yaml           # Shared route + physics (base world)
    ├── scenario-1.yaml
    ├── scenario-2.yaml
    ├── scenario-3.yaml
    ├── scenario-4.yaml
    └── scenario-5.yaml
```

---

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Opens on the **scenario dropdown**. Three tabs: scenario input, per-bus timetable,
per-station order. Sidebar: live weight sliders.

### Sanity checks

```bash
python verify.py             # One-line summary per scenario
python validator.py          # Strict invariant + determinism check
python self_test.py          # Full adversarial suite + curveballs
```

**Expected `verify.py` output** — all must show `range_viol=0` and `charger_overlap=0`:

```
scenario-1   buses=20 range_viol=0 charger_overlap=0 max_wait= 95 tot_wait= 900 last_arrival=08:35+1d
scenario-2   buses=20 range_viol=0 charger_overlap=0 max_wait=137 tot_wait=1446 last_arrival=08:35+1d
scenario-3   buses=14 range_viol=0 charger_overlap=0 max_wait= 95 tot_wait= 450 last_arrival=08:35+1d
scenario-4   buses=20 range_viol=0 charger_overlap=0 max_wait=145 tot_wait= 900 last_arrival=08:35+1d
scenario-5   buses=20 range_viol=0 charger_overlap=0 max_wait=153 tot_wait=1530 last_arrival=08:35+1d
```

**Expected `validator.py` output** — all hard invariants and determinism pass:

```
scenario-1   OK (20 buses) - all hard invariants hold, output deterministic
scenario-2   OK (20 buses) - all hard invariants hold, output deterministic
scenario-3   OK (14 buses) - all hard invariants hold, output deterministic
scenario-4   OK (20 buses) - all hard invariants hold, output deterministic
scenario-5   OK (20 buses) - all hard invariants hold, output deterministic

All scenarios pass every hard invariant.
```

> **Note:** `verify.py` is the quick human-readable smoke test (one-line summary per scenario).
> `validator.py` is the strict assertion suite — it re-derives every hard rule from the output
> and raises with a precise message the moment anything is wrong. Both are kept intentionally.

---

## Deploy (Streamlit Community Cloud)

1. Push the repo to a public GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub → **New app**.
3. Pick the repo and branch; set **Main file path** = `app.py`.
4. Click **Deploy** — Streamlit reads `requirements.txt` and installs everything automatically.
5. You get a public URL when it finishes.

> `app.py` and `scenarios/` must both sit at the repo root — scenarios are located relative to `app.py`.

---

## Change a weight (one line, data only)

Weights live only in the scenario YAML file — nowhere in the engine code.
In `scenarios/scenario-4.yaml`:

```yaml
# before
weights: { individual: 1.0, operator: 2.0, overall: 1.0 }

# after
weights: { individual: 1.0, operator: 0.5, overall: 1.0 }
```

No code changes. The engine reads the value via `weights.for_key(...)`.
The sidebar sliders in the UI do the same thing at runtime.

---

## Add a rule (no engine edits)

Rules are registered plugins. Adding a rule = decorating a class. The engine never changes.

### Soft rule — priority buses charge first

```python
# rules/soft.py
@soft_rule
class PriorityFirst(SoftRule):
    name = "S4-priority"
    weight_key = "overall"  # reuses an existing weight

    def cost(self, candidate, context):
        return -float(getattr(candidate.bus, "priority", 0))
```

Data: `{ id: bus-BK-10, ..., priority: 9 }`
Verified: bus-BK-10 moves from charger position 10 → 4 at station A.

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
Verified: that bus's plan changes from `{A, C}` → `{B, D}`. The engine was not edited.

---

## Architecture summary

The scheduler is a **deterministic discrete-event simulation** with a pluggable rule system:

1. **Plan selection** — for each bus, pick the minimal feasible charging plan that passes all hard rules.
2. **Event simulation** — a global time-ordered simulation runs all buses through the chargers. A bus's arrival at a downstream station reflects every wait absorbed upstream (schedules are coupled).
3. **Contention resolution** — when multiple buses wait at a charger, the next bus is the one minimising the weighted sum of soft-rule costs. Ties break on bus id for full determinism.

Two plugin seams are exposed:
- `HardRule.is_valid(plan, context)` — validates a proposed charging plan before simulation.
- `SoftRule.cost(candidate, context)` — scores a waiting bus at contention time.

The engine iterates the registries and reads everything else from the scenario. It references no station name, no bus count, and no rule name.

See `ARCHITECTURE.md` for the full design rationale, data-model decisions, anticipated change table, and honest status.

---

## Scenarios

| Scenario | Buses | Key feature |
|---|---|---|
| scenario-1 | 20 | Baseline — equal weights |
| scenario-2 | 20 | Higher contention |
| scenario-3 | 14 | Smaller fleet |
| scenario-4 | 20 | Mixed operators (kpn / freshbus / flixbus) — weight demo |
| scenario-5 | 20 | Busiest — highest total wait |