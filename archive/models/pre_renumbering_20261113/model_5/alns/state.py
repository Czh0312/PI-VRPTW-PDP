"""Search-state cloning, keys and result conversion for this model."""


def clone_state(state):
    return dict(
        deliveries={b: [list(r) for r in rows] for b, rows in state["deliveries"].items()},
        pickup_order={b: list(row) for b, row in state["pickup_order"].items()},
        pickup_vehicle={b: dict(row) for b, row in state["pickup_vehicle"].items()},
        e2=[{**r, "tasks": list(r["tasks"])} for r in state["e2"]],
    )


def state_key(state):
    return (
        tuple((b, tuple(map(tuple, rows))) for b, rows in state["deliveries"].items()),
        tuple((b, tuple(row)) for b, row in state["pickup_order"].items()),
        tuple((b, tuple(sorted(row.items()))) for b, row in state["pickup_vehicle"].items()),
        tuple(
            (
                r["satellite"],
                r["vehicle"],
                tuple(r["tasks"]),
                r.get("depart_after"),
                r.get("return_after"),
            )
            for r in state["e2"]
        ),
    )


def state_from_result(problem, result):
    state = dict(
        deliveries={
            b.id: [[] for _ in range(problem.fleet.e1_count)]
            for b in problem.operating_policy.batches
        },
        pickup_order={b.id: [] for b in problem.operating_policy.batches},
        pickup_vehicle={b.id: {} for b in problem.operating_policy.batches},
        e2=[],
    )
    for trip in result["e1_trips"]:
        batch, vehicle = trip["batch_id"], trip["vehicle_id"]
        for event in trip["events"]:
            site = event["site_id"]
            if event["kind"] == "d":
                state["deliveries"][batch][vehicle].append(site)
            else:
                state["pickup_order"][batch].append(site)
                state["pickup_vehicle"][batch][site] = vehicle
    for sites in state["pickup_order"].values():
        sites.extend(s for s in problem.satellites if s not in sites)
    for trip in sorted(
        result["e2_trips"], key=lambda r: (r["trip_index"], r["satellite_id"], r["vehicle_id"])
    ):
        state["e2"].append(
            dict(
                satellite=trip["satellite_id"],
                vehicle=trip["vehicle_id"],
                tasks=[(e["order_id"], e["kind"]) for e in trip["events"]],
            )
        )
        if len(problem.operating_policy.batches) > 1:
            state["e2"][-1].update(
                depart_after=trip["departure"], return_after=trip["return_complete"]
            )
    return state
