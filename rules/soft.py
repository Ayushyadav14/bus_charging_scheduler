"""Soft rules S1-S3, registered as plugins.

Each returns a RAW scalar where lower means "serve this bus sooner". The engine
min-max normalises each rule's raw values across the buses currently waiting at
a charger, multiplies by that rule's weight, sums, and serves the minimum
(tie-break by bus id). So a rule never needs to know the weights or the other
rules — it just expresses one preference.

Why these three disagree (which is what makes weights matter):
* S1 wants the bus that has *already waited longest* to go first.
* S2 wants the operator that has been *served least* so far to go first,
  interleaving operators so no fleet is systematically stuck behind another.
* S3 wants the bus with the *most remaining journey* to go first, protecting
  the latest network arrival (makespan).
"""
from __future__ import annotations

from rules.base import PickCandidate, SchedulingContext, SoftRule
from rules.registry import soft_rule


@soft_rule
class IndividualWait(SoftRule):  # S1
    name = "S1-individual"
    weight_key = "individual"

    def cost(self, candidate: PickCandidate, context: SchedulingContext) -> float:
        # Longer current wait -> more negative -> lower -> served sooner.
        return -float(candidate.wait_so_far)


@soft_rule
class OperatorFairShare(SoftRule):  # S2
    name = "S2-operator"
    weight_key = "operator"

    def cost(self, candidate: PickCandidate, context: SchedulingContext) -> float:
        # Fewer charges completed by this bus's operator -> lower -> served
        # sooner. This spreads charger time across operators ("runs smoothly as
        # a group"); raising the operator weight makes the spreading aggressive.
        return float(context.operator_completions.get(candidate.bus.operator, 0))


@soft_rule
class OverallTime(SoftRule):  # S3
    name = "S3-overall"
    weight_key = "overall"

    def cost(self, candidate: PickCandidate, context: SchedulingContext) -> float:
        # Greater remaining distance -> more negative -> lower -> served sooner,
        # so the bus that would otherwise arrive latest is protected.
        return -float(candidate.remaining_distance)