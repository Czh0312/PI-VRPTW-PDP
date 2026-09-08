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

M4/M7 also mix fixed-current-assignment route moves into their assignment
neighborhoods (`fixed_route_probability=0.8`; set zero for assignment-only
search). Each fixed move freezes a fresh M3 task catalogue before destruction,
uses M3 E1/E2 repair, and passes the resulting candidate through the full M4
evaluator. The learned destroy/repair indices now identify composite moves:
each index has both a route and an assignment implementation. The separate
`neighborhood_attempts` counts report the actual branch used. M7 pools accepted
assignments from both branches and includes refill attempts in these counts.

The M4 evaluator reuses task catalogues when assignments are unchanged, but
always checks the changed E1/E2 schedules. No insertion cache or frozen task
catalogue survives an attempt.

M6 defaults to `fast_stage1=True`: a plain M3 evaluator with bounded metric
caches handles the no-relay search, including refill. The relay evaluator owns
all mutable handoff locations and the final feasibility audit; its distances
are never cached this way. Set `fast_stage1=False` for the legacy stage-one
path. Explicit warm starts containing handoffs also retain that path. These
switches do not increase the requested wall-time or iteration budget.

Reproduce the paired wall-time probe (use Python 3.10 or newer):

```sh
PYTHONHASHSEED=0 python -m experiments.runs.run_alns_quality_probe \
  --output output/experiments/alns_quality_probe.jsonl --seconds 3
```

It records public routes, recomputed distance, full diagnostics, and runtime
for each model/instance/seed. Output creation is exclusive to protect earlier
results. Equal-time iteration throughput measures search speed, not a promise
of lower runtime for the same target quality or a proof of relay benefit.
