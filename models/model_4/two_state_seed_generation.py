"""Safe, explicitly non-complete seed-state generation for M4.

The direct-E1 singleton seeds here are useful to start a free-assignment
two-state RMP at every satellite.  They do *not* enumerate all possible h/p
states and may never be used as a global-exactness argument.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from models.model_4.certified_time_expansion import M4SatelliteProgram, make_m4_satellite_program


def generate_m4_direct_e1_singleton_programs(
    problem,
    *,
    customer_ids: Sequence[str] | None = None,
) -> tuple[M4SatelliteProgram, ...]:
    """Generate validated direct-depot E1 / one-customer E2 seed programs.

    For satellite ``s`` the delivery-ready time is the earliest possible E1
    completion on direct route ``D → s_d``.  Each accepted program has one
    E2 route ``s → c_d → c_p → s`` and its earliest valid pickup-ready time.
    The construction is a safe state *enrichment* heuristic: all emitted
    columns are physically feasible, but delayed E1 visits and multi-customer
    programs are intentionally left to pricing/state generation.
    """

    customers = {customer.id: customer for customer in problem.customers}
    selected_ids = tuple(customer_ids) if customer_ids is not None else tuple(customers)
    if len(set(selected_ids)) != len(selected_ids) or any(customer_id not in customers for customer_id in selected_ids):
        raise ValueError("customer_ids must be distinct identifiers in the problem")

    programs: list[M4SatelliteProgram] = []
    for satellite in problem.satellites:
        sat_id = satellite.id
        h = float(problem.travel_time(problem.depot.id, sat_id)) + float(satellite.service_time)
        for customer_id in selected_ids:
            try:
                programs.append(
                    make_m4_satellite_program(
                        problem,
                        sat_id,
                        [(sat_id, f"{customer_id}d", f"{customer_id}p", sat_id)],
                        ready_time=h,
                    )
                )
            except ValueError:
                # An infeasible direct singleton cannot be a seed.  Skipping
                # it is safe because this routine promises enrichment, not a
                # complete universe.
                continue
    return tuple(programs)
