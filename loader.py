"""Load a scenario file into a typed ``Scenario``.

Responsibilities:
* read YAML (or JSON) with a safe loader,
* resolve ``extends:`` by deep-merging a base world under the scenario,
* coerce/validate known fields into the typed model,
* preserve unknown fields into ``attrs`` / ``extensions`` so a future field
  never breaks the loader.
"""
from __future__ import annotations

import copy
import json
import os

import yaml

from model import (Bus, Direction, Node, Objective, Physics, Route, Scenario,
                   Weights)

_BUS_KNOWN = {"id", "operator", "direction", "depart", "priority",
              "allowed_stations", "min_charges"}
_NODE_KNOWN = {"id", "name", "kind", "chargers", "full_charge"}


def _read(path: str) -> dict:
    with open(path) as fh:
        text = fh.read()
    if path.endswith((".yaml", ".yml")):
        return yaml.safe_load(text)  # safe_load avoids arbitrary tags
    return json.loads(text)


def _deep_merge(base: dict, over: dict) -> dict:
    """Maps deep-merge; scalars and lists in ``over`` replace ``base``."""
    out = copy.deepcopy(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _parse_time(value) -> int:
    """'HH:MM' (or already-int minutes) -> minutes since midnight."""
    if isinstance(value, int):
        return value
    hours, minutes = str(value).split(":")
    return int(hours) * 60 + int(minutes)


def load_scenario(path: str) -> Scenario:
    raw = _read(path)
    if "extends" in raw:
        base_path = os.path.join(os.path.dirname(path), raw["extends"])
        raw = _deep_merge(_read(base_path), raw)
    return build_scenario(raw)


def build_scenario(raw: dict) -> Scenario:
    physics = Physics(**raw["physics"])

    rt = raw["route"]
    nodes = [
        Node(
            id=n["id"],
            name=n.get("name", n["id"]),
            kind=n["kind"],
            chargers=n.get("chargers", 0),
            full_charge=n.get("full_charge", False),
            attrs={k: v for k, v in n.items() if k not in _NODE_KNOWN},
        )
        for n in rt["nodes"]
    ]
    seg = {frozenset((s["from"], s["to"])): float(s["distance_km"])
           for s in rt["segments"]}
    directions = {k: Direction(id=k, from_id=v["from"], to_id=v["to"])
                  for k, v in rt["directions"].items()}
    route = Route(id=rt["id"], nodes=nodes, seg_km=seg, directions=directions)

    weights = Weights(**raw.get("weights", {}))
    objective = Objective(**raw.get("objective", {}))

    fleet = [
        Bus(
            id=b["id"],
            operator=b["operator"],
            direction=b["direction"],
            depart_minute=_parse_time(b["depart"]),
            priority=b.get("priority", 0),
            allowed_stations=tuple(b["allowed_stations"]) if b.get("allowed_stations") else None,
            min_charges=b.get("min_charges"),
            attrs={k: v for k, v in b.items() if k not in _BUS_KNOWN},
        )
        for b in raw["fleet"]
    ]

    return Scenario(
        id=raw.get("id", "scenario"),
        name=raw.get("name", ""),
        route=route,
        physics=physics,
        weights=weights,
        objective=objective,
        fleet=fleet,
        extensions=raw.get("extensions", {}),
    )