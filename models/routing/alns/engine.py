"""Run an ALNS trajectory from a scored starting state and model callbacks."""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Evaluation:
    """A scored state, including penalized states without a decoded result."""

    score: float
    state: Any
    result: Any


@dataclass
class Proposal:
    """A candidate and the current state after any restart."""

    current: Optional[Evaluation]
    candidate: Optional[Evaluation] = None
    operators: tuple[str, ...] = ()
    stop: bool = False


class OperatorWeights:
    def __init__(
        self, control, names, *, unweighted=(), segmented=False, keep_unused=False,
        rejected_reward=0.0, accepted_reward=1.0, downhill_reward=3.0,
        credit_rejected_best=True,
    ):
        self.control = control
        self.segmented = segmented
        self.keep_unused = keep_unused
        self.rejected_reward = rejected_reward
        self.accepted_reward = accepted_reward
        self.downhill_reward = downhill_reward
        self.credit_rejected_best = credit_rejected_best
        self.names = tuple(names)
        tracked = self.names + tuple(unweighted)
        self.weights = dict.fromkeys(self.names, control.parameters.initial_weight)
        self.attempts = dict.fromkeys(tracked, 0)
        self.improvements = dict.fromkeys(tracked, 0)
        self.noops = dict.fromkeys(tracked, 0)
        self.uses = dict.fromkeys(self.names, 0)
        self.rewards = dict.fromkeys(self.names, 0.0)

    def choose(self, rng, available=None):
        if available is None:
            index = self.control.select_index(rng, list(self.weights.values()))
            return self.names[index]
        return self.control.select_name(rng, available, self.weights)

    def record_attempts(self, names):
        for name in names:
            self.attempts[name] += 1
            if self.segmented and name in self.weights:
                self.uses[name] += 1

    def update(self, names, iterations, *, evaluated, improved, accepted, downhill):
        reward = self.rejected_reward
        if improved and (accepted or self.credit_rejected_best):
            reward = 8.0
        elif accepted:
            reward = self.downhill_reward if downhill else self.accepted_reward
        p = self.control.parameters
        for name in names:
            self.improvements[name] += int(improved)
            self.noops[name] += int(not evaluated)
            if name not in self.weights:
                continue
            if self.segmented:
                self.rewards[name] += reward if evaluated else 0.0
            else:
                increment = p.online_reaction * reward if evaluated else p.noop_increment
                self.weights[name] = p.online_retention * self.weights[name] + increment
        if self.segmented and iterations % p.segment_period == 0:
            for name, weight in self.weights.items():
                uses = self.uses[name]
                if uses or not self.keep_unused:
                    average = self.rewards[name] / uses if uses else weight
                    self.weights[name] = max(p.minimum_weight,
                                             p.segment_retention * weight + p.segment_reaction * average)
                self.uses[name] = 0
                self.rewards[name] = 0.0


def run_search(current, control, weights, propose, remember, accept, *,
               max_iterations=None, deadline=None):
    """Apply evaluated proposals, record incumbents and adapt operator weights.

    The model supplies initial states and proposal evaluation. A proposal can
    restart the current state, report an operator no-op, or stop after an
    expensive move. Incumbents are considered before the acceptance decision.
    """
    if max_iterations is None:
        max_iterations = control.parameters.max_iterations
    iterations = accepted = 0
    for iteration in range(max_iterations):
        now = control.clock()
        expired = (now - control.begun >= control.parameters.max_time
                   if deadline is None else now >= deadline)
        if expired:
            break
        iterations = iteration + 1
        proposal = propose(current, iteration)
        current = proposal.current
        weights.record_attempts(proposal.operators)
        if proposal.stop:
            break
        candidate = proposal.candidate
        improved = accepted_move = downhill = False
        if candidate is not None:
            improved = remember(proposal, iterations)
            accepted_move = accept(proposal, iteration, improved)
            if accepted_move:
                downhill = candidate.score < current.score
                current = candidate
                accepted += 1
        weights.update(proposal.operators, iterations, evaluated=candidate is not None,
                       improved=improved, accepted=accepted_move, downhill=downhill)
    return current, iterations, accepted
