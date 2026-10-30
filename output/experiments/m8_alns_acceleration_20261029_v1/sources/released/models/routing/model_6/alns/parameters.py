"""Search budgets and adaptive/annealing constants for this model."""
from dataclasses import dataclass


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
