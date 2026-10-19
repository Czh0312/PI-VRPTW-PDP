"""Adaptive operator selection and acceptance; no routing or solver imports."""
from dataclasses import dataclass
from typing import Callable
import math

from models.routing.model_3.alns.parameters import ALNSParameters


@dataclass
class SearchControl:
    parameters: ALNSParameters
    begun: float
    clock: Callable[[], float]

    def initial_weights(self, operators):
        return [self.parameters.initial_weight] * len(operators)

    def initial_weight_dict(self, operators):
        return {op: self.parameters.initial_weight for op in operators}

    def select_index(self, rng, weights):
        return rng.choices(range(len(weights)), weights=weights, k=1)[0]

    def select_name(self, rng, available, weights):
        return rng.choices(available, weights=[weights[name] for name in available])[0]

    def progress(self, iteration):
        p = self.parameters
        return min(
            1.0,
            max(
                (iteration + 1) / max(1, p.max_iterations), (self.clock() - self.begun) / p.max_time
            ),
        )

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
        # Preserve short-circuiting: an improving move consumes no random draw.
        if new_score <= score:
            return True
        exponent = (score - new_score) / temperature
        exponent = max(-700.0, exponent) if joint else min(0, exponent)
        return rng.random() < math.exp(exponent)

    def penalize_noop(self, weights, index):
        p = self.parameters
        weights[index] = p.online_retention * weights[index] + p.noop_increment

    def reward_online(self, weights, index, reward):
        p = self.parameters
        weights[index] = p.online_retention * weights[index] + p.online_reaction * reward

    def update_segment(self, weights, rewards, uses):
        p = self.parameters
        return [
            max(
                p.minimum_weight, p.segment_retention * w + p.segment_reaction * (r / u if u else w)
            )
            for w, r, u in zip(weights, rewards, uses)
        ]

    def update_named_segment(self, operators, weights, rewards, uses):
        p = self.parameters
        for name in operators:
            weights[name] = max(
                p.minimum_weight,
                p.segment_retention * weights[name]
                + p.segment_reaction
                * (rewards[name] / uses[name] if uses[name] else weights[name]),
            )
            rewards[name] = 0.0
            uses[name] = 0
