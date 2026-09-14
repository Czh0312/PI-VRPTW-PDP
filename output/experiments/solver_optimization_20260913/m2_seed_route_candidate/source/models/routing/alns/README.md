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
search). Each fixed move selects the complete assignment's M3 task catalogue before destruction,
uses M3 E1/E2 repair, and passes the resulting candidate through the full M4
evaluator. The learned destroy/repair indices now identify composite moves:
each index has both a route and an assignment implementation. The separate
`neighborhood_attempts` counts report the actual branch used. M7 pools accepted
assignments from both branches and includes refill attempts in these counts.

The M4 evaluator reuses task catalogues when assignments are unchanged, but
always checks the changed E1/E2 schedules. Insertion options remain scoped to
an attempt. Up to eight fixed evaluators may now survive, keyed by the complete
assignment; each receives the current deadline. Rejected partial assignments
cannot reuse a different assignment's catalogue.

`evaluation_cache.py` caches immutable geometry and service times for M1–M4.
Route costs use the complete path as the key; schedules also include the
satellite and absolute departure time. The relay evaluator is excluded because
handoff locations and loads are mutable. M2 exposes the experimental
`fixed_route_probability=0.0`, using M1 route moves when enabled; its default
remains assignment search because the quality probe was inconclusive.

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

### M4 small-instance exchange polishing

For at most 20 customers with time-based cooling, M4 defaults to
`exchange_polish=True`. It reserves at most `min(0.05, max_time * 0.01)` seconds
within the caller's total budget. The main search retains its nominal cooling,
stagnation and restart schedule; the engine's optional `search_time_limit`
changes only its hard deadline. The final pass exchanges complete customer
delivery/pickup identities or same-satellite route suffixes, accepting only
strict improvements validated against the complete M4 constraints.

Set `exchange_polish=False` to reproduce the unpolished baseline. Large
instances and iteration-cooled runs do not enable this pass automatically.
`exchange_polish_improvements` and `exchange_polish_seconds` report its work.
The experimental `search_phase_fraction` remains 1.0 by default: restarting
learning in two phases did not demonstrate an overall advantage.

M7 now reserves the same small exchange pass inside its first-stage budget.
Additional experiments (`alns_config_overrides={"segment_reversal": True}` and
`seed_search_fraction`) remain disabled by default because short-budget probes
did not show stable quality gains. The latest complete comparison and routes
are in `output/experiments/solver_optimization_20260913/report.md`.

The final 22-instance, three-seed paired validation is recorded in
`output/experiments/m4_exchange_schedule_20260908/report.md`. All 132 routes
passed independent audits. This is empirical quality improvement, not an
optimality guarantee or a guarantee that every seed improves.
