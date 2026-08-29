# ALNS architecture

Models 1, 2, 3, 4 and 6 share the control loop in `engine.py` but keep
model-specific neighborhoods under `variants/`.

The engine learns destroy-repair pairs rather than independent operator
weights. A segment closes when either enough attempts have accumulated or the
wall-clock segment limit expires. This is important for expensive models such
as Model 4, which may execute fewer than 100 iterations in a complete run.

Every successful solver response with `return_info=True` includes
`alns_diagnostics`:

- number of adaptive weight updates, including the subset completed online
  early enough to affect the run;
- whether the simulated-annealing temperature was calibrated from observed
  positive cost deltas;
- per destroy-repair pair attempts, feasible candidates, accepted candidates,
  improvements, new best solutions, runtime and final weight.

Model-specific additions:

- Model 1: contiguous route-segment destruction;
- Models 2 and 4: assignment-boundary and two-satellite destruction, plus
  multi-seed initial assignments;
- Models 3 and 6: complete pickup-delivery pair repair across one or two routes;
- Model 6: one-exchange handoff search that can remove, move or replace an
  earlier greedy handoff.

The application service enables strong initial solutions and the standard
time-based restart/reheating policy. Direct solver calls can override every
engine setting through `alns_config_overrides`.
