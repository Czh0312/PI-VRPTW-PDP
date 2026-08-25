"""Regression checks for the non-certifying M4 two-state route primitive."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.run_mainline_small_exact import _fleet, _problem
from models.model_4.exact import solve_exact as solve_m4_exact
from models.model_4.single_satellite_exact_bp import (
    solve_single_satellite_exact_branch_price,
)
from models.model_4.certified_time_expansion import (
    forward_m4_route,
    make_m4_satellite_program,
    tighten_m4_program_pickup_state,
    validate_m4_e1_program_witness,
    validate_m4_satellite_schedule,
)
from models.model_4.two_state_program_master import solve_m4_two_state_program_pool
from models.model_4.two_state_global_program_master import (
    solve_m4_two_state_global_program_pool,
)
from models.model_4.two_state_global_root_cg import run_m4_two_state_fixed_state_root_cg
from models.model_4.two_state_fixed_grid_branch_price import (
    solve_m4_two_state_fixed_grid_branch_price,
)
from models.model_4.two_state_program_pricing import price_m4_satellite_program
from models.model_4.two_state_program_import import (
    import_m4_routes_as_two_state_programs,
)
from models.model_4.two_state_seed_generation import (
    generate_m4_direct_e1_singleton_programs,
)


def main() -> None:
    problem = _problem(ROOT / "data" / "5_customers" / "C101.csv", _fleet())

    # Same-route delivery then pickup needs no cross-route master condition.
    paired = forward_m4_route(
        problem, "S0", ("S0", "C0d", "C0p", "S0"), ready_time=0.0
    )
    assert paired.feasible, paired.reason
    assert not paired.external_delivery_dependencies
    assert paired.return_time is not None

    witness = validate_m4_satellite_schedule(
        problem, "S0", [("S0", "C0d", "C0p", "S0")],
        ready_time=0.0, pickup_ready_time=paired.return_time,
    )
    assert witness.feasible, witness.reason
    assert len(witness.route_return_times) == 1
    too_early = validate_m4_satellite_schedule(
        problem, "S0", [("S0", "C0d", "C0p", "S0")],
        ready_time=0.0, pickup_ready_time=paired.return_time - 1.0,
    )
    assert not too_early.feasible
    assert too_early.reason and "returns after" in too_early.reason

    program = make_m4_satellite_program(
        problem, "S0", [("S0", "C0d", "C0p", "S0")], ready_time=0.0
    )
    assert program.customers == ("C0",)
    assert program.pickup_ready_time >= program.schedule.route_return_times[0]
    assert program.cost > 0.0
    loose_program = make_m4_satellite_program(
        problem,
        "S0",
        [("S0", "C0d", "C0p", "S0")],
        ready_time=0.0,
        pickup_ready_time=paired.return_time + 50.0,
    )
    tightened = tighten_m4_program_pickup_state(problem, loose_program)
    assert tightened is not None
    assert abs(tightened.pickup_ready_time - paired.return_time) <= 1e-6
    assert tightened.routes == loose_program.routes
    assert tighten_m4_program_pickup_state(problem, tightened) is None
    pool = solve_m4_two_state_program_pool(["C0"], ["S0"], [program])
    assert pool["status"] == "OPTIMAL"
    assert not pool["proven_optimal"]
    assert pool["proof_scope"] == "restricted_m4_two_state_program_pool_only"

    e1_ready = problem.travel_time(problem.depot.id, "S0") + problem.nodes["S0"].service_time
    e1_program = make_m4_satellite_program(
        problem, "S0", [("S0", "C0d", "C0p", "S0")], ready_time=e1_ready
    )
    linked = validate_m4_e1_program_witness(
        problem, [(problem.depot.id, "S0d", "S0p", problem.depot.id)], {"S0": e1_program}
    )
    assert linked.feasible, linked.reason

    # M4 permits delivery and pickup for one satellite to use different E1
    # vehicles.  The pickup vehicle must start empty rather than inheriting
    # the delivery load from the other vehicle.
    split_e1 = validate_m4_e1_program_witness(
        problem,
        [
            (problem.depot.id, "S0d", problem.depot.id),
            (problem.depot.id, "S0p", problem.depot.id),
        ],
        {"S0": e1_program},
    )
    assert split_e1.feasible, split_e1.reason

    # The global restricted master retains the E1 routing/assignment layer.
    # One program that serves all five customers at S0 has a finite (h, p)
    # state, and its integer solution is feasible but deliberately cannot be
    # reported as a global optimum of the unrestricted M4 problem.
    all_customer_program = make_m4_satellite_program(
        problem,
        "S0",
        [("S0", f"{customer.id}d", f"{customer.id}p", "S0") for customer in problem.customers],
        ready_time=e1_ready,
    )
    global_pool = solve_m4_two_state_global_program_pool(
        problem, [all_customer_program], problem.fleet.e2_per_sat_count
    )
    assert global_pool["status"] == "OPTIMAL"
    assert not global_pool["proven_optimal"]
    assert global_pool["proof_scope"] == "restricted_m4_two_state_global_program_pool_only"
    assert global_pool["selected_programs"] == [all_customer_program]
    expected = all_customer_program.cost + 2.0 * problem.distance(problem.depot.id, "S0")
    assert abs(global_pool["objective"] - expected) <= 1e-6
    fixed_branch_pool = solve_m4_two_state_global_program_pool(
        problem,
        [all_customer_program],
        problem.fleet.e2_per_sat_count,
        fixed_assignment={"C0": "S0"},
    )
    assert fixed_branch_pool["status"] == "OPTIMAL"
    assert not fixed_branch_pool["proven_optimal"]

    # A root-CG pass gets its pricing coefficients from the actual global
    # RMP rows.  The deliberately tight iteration cap must remain explicitly
    # non-certified even after it generates a valid negative program.
    root_cg = run_m4_two_state_fixed_state_root_cg(
        problem,
        [all_customer_program],
        problem.fleet.e2_per_sat_count,
        max_iterations=1,
        pricing_time_limit=10.0,
    )
    assert root_cg.status == "FIXED_STATE_CG_ITERATION_LIMIT"
    assert not root_cg.proven_optimal
    assert root_cg.proof_scope == "restricted_existing_m4_two_state_grid_root_lp_only"
    assert len(root_cg.columns) == 2
    assert root_cg.pricing_records[0]["reduced_cost"] < -1e-6

    closed_root = run_m4_two_state_fixed_state_root_cg(
        problem,
        [all_customer_program],
        problem.fleet.e2_per_sat_count,
        max_iterations=30,
        pricing_time_limit=10.0,
    )
    assert closed_root.status == "FIXED_STATE_ROOT_LP_CLOSED"
    assert not closed_root.proven_optimal
    assert len(closed_root.columns) > 1
    # This is the fixed-S0 LP reference only; it demonstrates complete
    # pricing on the existing state grid, not a free-assignment M4 proof.
    assert abs(closed_root.root_lp_objective - 391.5706374499058) <= 1e-6

    # A certified arc-flow solution can be imported as h/p program seeds;
    # this is the entry point used later for 10-customer incumbent seeding.
    e1_routes, e2_routes_by_sat, arc_assignment, arc_info = solve_m4_exact(
        problem,
        problem.fleet.e2_per_sat_count,
        time_limit=60.0,
        verbose=False,
        return_info=True,
    )
    assert arc_info["proven_optimal"]
    imported = import_m4_routes_as_two_state_programs(
        problem, e1_routes, e2_routes_by_sat
    )
    assert imported.e1_witness.feasible, imported.e1_witness.reason
    assert dict(imported.assignment) == arc_assignment
    assert len(imported.programs) == 1

    def route_arcs(program):
        return {
            (left, right)
            for route in program.routes
            for left, right in zip(route, route[1:])
        }

    imported_program = imported.programs[0]
    e2_branch_arc = next(iter(route_arcs(imported_program) - route_arcs(all_customer_program)))
    e2_arc_forbidden_pool = solve_m4_two_state_global_program_pool(
        problem,
        [all_customer_program, imported_program],
        problem.fleet.e2_per_sat_count,
        fixed_e2_arcs={("S0", 0, *e2_branch_arc): 0},
    )
    assert e2_arc_forbidden_pool["status"] == "OPTIMAL"
    assert e2_arc_forbidden_pool["selected_programs"] == [all_customer_program]
    e2_arc_required_price = price_m4_satellite_program(
        problem,
        "S0",
        delivery_ready_time=imported_program.ready_time,
        pickup_ready_time=imported_program.pickup_ready_time,
        customer_rc_terms={customer.id: -10000.0 for customer in problem.customers},
        required_arcs=(e2_branch_arc,),
        time_limit=30.0,
    )
    assert e2_arc_required_price.pricing_optimal
    assert e2_arc_required_price.program is not None
    assert e2_branch_arc in route_arcs(e2_arc_required_price.program)

    # With exactly one satellite, the direct E1 delivery time and universal
    # pickup-ready horizon cover every feasible M4 program.  In this special
    # subclass a closed B&P tree is therefore a true global certificate.
    single_sat_exact = solve_single_satellite_exact_branch_price(
        problem,
        problem.fleet.e2_per_sat_count,
        seed_time_limit=30.0,
        pricing_time_limit=10.0,
        max_nodes=64,
        max_cg_iterations_per_node=30,
    )
    assert single_sat_exact.info["proven_optimal"]
    assert single_sat_exact.info["proof_scope"] == "full_single_satellite_branch_price"
    assert abs(single_sat_exact.info["objective"] - arc_info["objective"]) <= 1e-6

    fixed_grid_bcp = solve_m4_two_state_fixed_grid_branch_price(
        problem,
        imported.programs,
        problem.fleet.e2_per_sat_count,
        max_nodes=64,
        max_cg_iterations_per_node=30,
        pricing_time_limit=10.0,
    )
    assert fixed_grid_bcp.status == "FIXED_GRID_OPTIMAL"
    assert fixed_grid_bcp.fixed_grid_proven_optimal
    # A fixed-grid proof excludes unseen h/p states, hence remains distinct
    # from the public/global exactness flag.
    assert not fixed_grid_bcp.proven_optimal
    assert fixed_grid_bcp.proof_scope == "complete_branch_price_for_initial_m4_two_state_grid_only"
    assert fixed_grid_bcp.nodes_processed == 1
    assert abs(fixed_grid_bcp.objective - 391.5706374499058) <= 1e-6

    # Add five additional singleton states.  They must not suppress a
    # multi-customer program at the original state merely because it contains
    # an already-known singleton route; this guards the exact no-good logic
    # used to avoid rediscovering bounded columns during pricing.
    direct_singletons = generate_m4_direct_e1_singleton_programs(problem)
    assert len(direct_singletons) == 5
    enriched_grid_bcp = solve_m4_two_state_fixed_grid_branch_price(
        problem,
        [all_customer_program, *direct_singletons],
        problem.fleet.e2_per_sat_count,
        max_nodes=16,
        max_cg_iterations_per_node=20,
        pricing_time_limit=5.0,
        exclude_existing_pricing_programs=True,
    )
    assert enriched_grid_bcp.status == "FIXED_GRID_OPTIMAL"
    assert enriched_grid_bcp.fixed_grid_proven_optimal
    assert abs(enriched_grid_bcp.objective - 391.5706374499058) <= 1e-6

    # Fixed-state pricing builds a whole M4 satellite program, not a single
    # route.  A strongly negative coefficient for C0 finds the validated
    # C0d→C0p program and returns a negative reduced cost.
    priced = price_m4_satellite_program(
        problem,
        "S0",
        delivery_ready_time=0.0,
        pickup_ready_time=paired.return_time,
        customer_rc_terms={
            customer.id: (-10000.0 if customer.id == "C0" else 10000.0)
            for customer in problem.customers
        },
        time_limit=30.0,
    )
    assert priced.pricing_optimal, priced.status
    assert priced.reduced_cost is not None and priced.reduced_cost < -1e-6
    assert priced.selected_customers == ("C0",)
    assert priced.programs == (priced.program,)
    assert len(priced.program_reduced_costs) == 1
    assert priced.program_reduced_costs[0] < -1e-6

    # Pricing closure need not optimize the minimum reduced cost: infeasibility
    # of ``rc <= -eps`` is already a complete no-negative-column proof.
    no_negative = price_m4_satellite_program(
        problem,
        "S0",
        delivery_ready_time=0.0,
        pickup_ready_time=paired.return_time,
        customer_rc_terms={customer.id: 10000.0 for customer in problem.customers},
        prove_no_negative_only=True,
        time_limit=30.0,
    )
    assert no_negative.pricing_closed
    assert no_negative.status == "NO_NEGATIVE_COLUMN_PROVED"
    assert not no_negative.programs
    assert priced.program is not None
    assert priced.program.routes == (("S0", "C0d", "C0p", "S0"),)

    branch_priced = price_m4_satellite_program(
        problem,
        "S0",
        delivery_ready_time=0.0,
        pickup_ready_time=all_customer_program.pickup_ready_time,
        customer_rc_terms={
            customer.id: (-10000.0 if customer.id == "C1" else 10000.0)
            for customer in problem.customers
        },
        required_customer_ids=("C1",),
        forbidden_customer_ids=("C0",),
        time_limit=30.0,
    )
    assert branch_priced.pricing_optimal, branch_priced.status
    assert branch_priced.selected_customers == ("C1",)
    assert branch_priced.program is not None

    # A pickup-only route cannot become an unconditional column: its matching
    # delivery must be supplied by a different selected E2 route/master row.
    split = forward_m4_route(
        problem, "S0", ("S0", "C0p", "S0"), ready_time=0.0
    )
    assert split.feasible, split.reason
    assert split.external_delivery_dependencies == ("C0",)

    # If the delivery exists on this same route, pickup-before-delivery is
    # physically impossible and cannot be treated as an external dependency.
    reversed_pair = forward_m4_route(
        problem, "S0", ("S0", "C0p", "C0d", "S0"), ready_time=0.0
    )
    assert not reversed_pair.feasible
    assert reversed_pair.reason and "precedes" in reversed_pair.reason

    print("M4 two-state route-expansion regression passed")


if __name__ == "__main__":
    main()
