"""Route topology, physical vehicle order, and optional container-wait links."""
from dataclasses import dataclass, field
from copy import deepcopy


@dataclass
class Route:
    layer: str
    base: str
    vehicle: int
    actions: list


@dataclass
class State:
    routes: dict = field(default_factory=lambda: dict(e1=[], e2=[]))
    reuse: list = field(default_factory=list)

    def copy(self):
        return deepcopy(self)


def groups(problem, route):
    result = []
    for oid, kind in route.actions:
        o = problem.order_map[oid]
        site = o.satellite if route.layer == 'e1' else o.customer
        if route.layer == 'e1' and result and result[-1][:2] == (site,kind):
            result[-1][2].append(oid)
        else:
            result.append((site, kind, [oid]))
    return result


def route_distance(problem, route):
    sites = [route.base]+[x[0] for x in groups(problem,route)]+[route.base]
    return sum(problem.distance(a,b) for a,b in zip(sites,sites[1:]))


def capacity_ok(problem, route):
    f = problem.fleet
    capw = f.e1_weight_cap if route.layer == 'e1' else f.e2_weight_cap
    capv = f.e1_volume_cap if route.layer == 'e1' else f.e2_volume_cap
    w = sum(problem.task(oid,kind)[1] for oid,kind in route.actions if kind == 'd')
    v = sum(f.container_volume for _,kind in route.actions if kind == 'd')
    if w > capw+1e-7 or v > capv+1e-7:
        return False
    for oid,kind in route.actions:
        sign = -1 if kind == 'd' else 1
        w += sign*problem.task(oid,kind)[1]; v += sign*f.container_volume
        if not -1e-7 <= w <= capw+1e-7 or not -1e-7 <= v <= capv+1e-7:
            return False
    return True
