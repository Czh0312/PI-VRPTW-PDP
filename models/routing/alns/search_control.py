"""Budgets, operator selection and simulated annealing."""
from dataclasses import dataclass
from typing import Callable
import math


@dataclass(frozen=True)
class ALNSParameters:
    max_time: float
    max_iterations: int
    seed: int
    initial_weight: float = 1.0
    online_retention: float = 0.9
    online_reaction: float = 0.1
    noop_increment: float = 0.01
    segment_period: int = 25
    segment_retention: float = 0.8
    segment_reaction: float = 0.2
    minimum_weight: float = 0.1
    route_minimum_temperature: float = 0.01
    single_batch_temperature_scale: float = 0.05
    multibatch_temperature_scale: float = 0.01
    joint_minimum_temperature: float = 0.000001
    joint_temperature_scale: float = 0.04


@dataclass
class SearchControl:
    parameters: ALNSParameters
    begun: float
    clock: Callable[[], float]

    def select_index(self, rng, weights):
        return rng.choices(range(len(weights)), weights=weights, k=1)[0]

    def select_name(self, rng, available, weights):
        return rng.choices(available, weights=[weights[name] for name in available])[0]

    def progress(self, iteration):
        p = self.parameters
        return min(1.0, max(
            (iteration + 1) / max(1, p.max_iterations),
            (self.clock() - self.begun) / p.max_time,
        ))

    def time_fraction(self):
        return min(1.0, (self.clock() - self.begun) / self.parameters.max_time)

    def route_temperature(self, reference, progress, *, multibatch=False):
        p = self.parameters
        scale = p.multibatch_temperature_scale if multibatch else p.single_batch_temperature_scale
        return max(p.route_minimum_temperature, reference * scale * (1 - progress) ** 2)

    def joint_temperature(self, best_cost, score, fraction):
        p = self.parameters
        return max(
            p.joint_minimum_temperature,
            max(1.0, best_cost if math.isfinite(best_cost) else score)
            * p.joint_temperature_scale
            * (1 - fraction),
        )

    def accept(self, rng, score, new_score, temperature, *, joint=False):
        # Improving and equal-score moves consume no random draw.
        if new_score <= score:
            return True
        exponent = (score - new_score) / temperature
        exponent = max(-700.0, exponent) if joint else min(0, exponent)
        return rng.random() < math.exp(exponent)

    def accept_joint(self, rng, score, new_score, best_cost):
        temperature = self.joint_temperature(best_cost, score, self.time_fraction())
        return self.accept(rng, score, new_score, temperature, joint=True)
