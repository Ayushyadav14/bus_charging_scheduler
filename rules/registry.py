"""Rule registry. A rule joins the engine by decorating its class — the engine
iterates whatever is registered and never names a rule itself."""
from __future__ import annotations
 
from rules.base import HardRule, SoftRule
 
HARD_RULES: list = []
SOFT_RULES: list = []
 
 
def hard_rule(cls):
    """@hard_rule — register a HardRule instance. Adding a rule = a class."""
    HARD_RULES.append(cls())
    return cls
 
 
def soft_rule(cls):
    """@soft_rule — register a SoftRule instance. The engine never changes."""
    SOFT_RULES.append(cls())
    return cls
 