## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-05
- Verification Status: ANALYZED (mathematical projection argument and tests; timed comparison pending)
- Version Label: m3_physical_bounds_v1

## Projection argument

The input is a fixed-assignment, one-batch reusable M3 instance. No M4 assignment decision is fixed by this method: M4 is rejected. For each originally active site, select exactly that site's orders while retaining the full original road network, fleet and operating policy. Build the existing single-batch integer formulation. Delete all E1 route variables and every linear or indicator row incident to them. Retain the original necessary supply-time lower/upper bounds, all E2 task clocks, capacities, same-order delivery-before-pickup constraints, integer clocks, physical vehicle route slots, preparation, turnaround and ordering. Pure leftover E1 auxiliary flags are free to take values and cannot impose E2 restrictions through deleted rows.

Every feasible original full schedule induces a feasible site model by retaining all its local trips and clocks, its actual E1 supply completion, and relabeling homogeneous vehicles by trip count. Restricting orders does not remove any mandatory local task, alter any customer/order identity, or alter a road arc's travel time or objective coefficient. Original global stock suffices for the local subset; the retained one-batch stock constant is weaker. Necessary bounds based on the full road network stay valid even for directed nonmetric times. No route set is truncated.

The site's objective is the original raw E2 distance, obtained by deleting E1 objective terms. A finite Gurobi ObjBound, with a conservative numerical margin subtracted, is therefore an unconditional floor on that site's cost in every original feasible schedule. Timed-out local primal objectives are never lower bounds. Unknown and infeasible local states do not yield a new positive cost floor. A contradiction with an audited initial schedule raises an error instead of promoting a bound.

The original full MILP receives one constraint per positive site floor. Every compressed internal arc variable is included once, despite appearing in multiple route-slot dictionaries. Floors for disjoint sites may be summed as an E2-only lower bound; this sum must not be added to an existing total-distance bound. The wrapper takes the maximum of the original full-model bound and that E2-only floor. The original full solver alone sets its integer proof flags. Local optimality is never promoted to original global optimality.

## Correctness evidence before timed comparisons

38 targeted tests passed in 1.24 seconds: independent all-permutation local one-trip oracles with a separately implemented Floyd replay, original full integer enumeration, forced compressed and uncompressed representations, duplicate-address orders, zero/nonmetric travel, fixed path/time projection, same physical vehicle across multiple trips, exhausted-budget incumbent preservation, scope/fingerprint/numeric guards.

Both original 20-customer input schedules were audited and physically projected before any timed comparison. Every local route path and integer start/return/event clock was fixed, each site model solved with the same original site distance, and all 40 tasks were retained for each input. This validates original schedules, not all possible schedules; the general argument is above.

406 relevant tests passed in the immutable candidate source in 11.20 seconds, including the 38 targeted tests. This is not a regression claim for other concurrently changed workspace files.

## Limits

No complete Benders master, feasibility separation, supply-threshold cuts, M4 free-assignment decomposition or new integer proof algorithm is implemented here. This isolated mechanism tests whether stronger unconditional physical-site bounds repay a 20% preprocessing allocation. Timed comparisons and independent frozen-MPS checks are separate and all outcomes will be retained. The machine is shared; postponing our launch cannot guarantee controlled host load.
