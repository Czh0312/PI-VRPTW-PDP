"""Full E1/E2 route, timing and homogeneous-container audit; no frozen routes."""
from collections import Counter, defaultdict
from copy import deepcopy
import math

TOL = 1e-6


def event_id(node):
    return node[5:] if node.startswith('MEET@') else node


def state_from_routes(problem, vehicles_per_satellite, routes, transfers=None):
    """Copy an optional starting solution and include every available vehicle.

    Routes are an initial state, never fixed decisions or a mathematical baseline.
    """
    e1, e2 = deepcopy(routes)
    if len(e1) > problem.e1_vehicle_count:
        raise ValueError('E1 车辆数超限')
    satellites = {s.id for s in problem.satellites}
    if set(e2) - satellites:
        raise ValueError('未知卫星仓')
    e1 += [[problem.depot.id, problem.depot.id] for _ in range(problem.e1_vehicle_count - len(e1))]
    for sat in satellites:
        paths = e2.setdefault(sat, [])
        if len(paths) > vehicles_per_satellite:
            raise ValueError('E2 车辆数超限')
        paths += [[sat, sat] for _ in range(vehicles_per_satellite - len(paths))]
    return {'e1_routes': e1, 'e2_routes': e2, 'transfers': deepcopy(transfers or [])}


def handoff_batches(transfers):
    """Group pairwise flows into one deposit and its asynchronous collections."""
    batches = {}
    for transfer in transfers:
        key = (transfer['satellite'], transfer['event'], transfer['sender_vehicle'])
        batch = batches.setdefault(key, {
            'satellite': key[0], 'event': key[1], 'sender_vehicle': key[2],
            'physical_location': transfer['physical_location'],
            'deposit_ready_time': transfer['handoff_ready_time'],
            'deposited_quantity': 0, 'receivers': {}})
        batch['deposited_quantity'] += transfer['quantity']
        receiver = batch['receivers'].setdefault(transfer['receiver_vehicle'], {
            'receiver_vehicle': transfer['receiver_vehicle'], 'quantity': 0,
            'pickup_time': transfer['receiver_pickup_time']})
        receiver['quantity'] += transfer['quantity']
    result = []
    for batch in batches.values():
        collections = sorted(batch.pop('receivers').values(),
                             key=lambda r: (r['pickup_time'], r['receiver_vehicle']))
        inventory = batch['deposited_quantity']
        for collection in collections:
            inventory -= collection['quantity']
            collection['inventory_after_pickup'] = inventory
        result.append({**batch, 'collections': collections})
    return result


class Evaluator:
    def __init__(self, problem, e1_tasks, e2_tasks, vehicles_per_satellite, TFp=9999.0):
        if isinstance(vehicles_per_satellite, bool) or not isinstance(vehicles_per_satellite, int) or vehicles_per_satellite < 1:
            raise ValueError('vehicles_per_satellite 必须是正整数')
        self.problem, self.tfp = problem, TFp
        self.vehicles_per_satellite = vehicles_per_satellite
        self.satellites = {s.id for s in problem.satellites}
        self.e1 = {t.id: t for t in e1_tasks}
        self.tasks = {t.id: t for ts in e2_tasks.values() for t in ts}
        self.sat = {t.id: s for s, ts in e2_tasks.items() for t in ts}
        self.cw = float(problem.fleet.container_weight)
        self.cv = float(problem.fleet.container_volume)
        self.boxes = {}
        for n, t in self.tasks.items():
            if t.kind != 'p':
                continue
            ratios = []
            for load, unit in ((t.w, self.cw), (t.v, self.cv)):
                if unit > TOL:
                    ratios.append(load / unit)
                elif load > TOL:
                    raise ValueError('无法换算同质空箱数量')
            if not ratios or round(ratios[0]) <= 0 or any(
                    abs(x - round(ratios[0])) > TOL for x in ratios):
                raise ValueError('回收量必须为同质空箱的正整数倍')
            self.boxes[n] = int(round(ratios[0]))
        self._dist, self._travel = {}, {}

    def phys(self, node):
        n = event_id(node)
        t = self.tasks.get(n) or self.e1.get(n)
        return t.cust if t else n

    def distance(self, a, b):
        key = (self.phys(a), self.phys(b))
        if key not in self._dist:
            self._dist[key] = self.problem.distance(*key)
        return self._dist[key]

    def travel(self, a, b):
        key = (self.phys(a), self.phys(b))
        if key not in self._travel:
            self._travel[key] = self.problem.travel_time(*key)
        return self._travel[key]

    def service(self, n):
        return float(getattr(self.problem.nodes[self.phys(n)], 'service_time', 0))

    def route_cost(self, route):
        return sum(self.distance(a, b) for a, b in zip(route, route[1:]))

    def cost(self, state):
        return sum(self.route_cost(r) for r in state['e1_routes']) + sum(
            self.route_cost(r) for paths in state['e2_routes'].values() for r in paths)

    def transfer_maps(self, state):
        sent, received = defaultdict(int), defaultdict(int)
        for t in state['transfers']:
            q = t['quantity']
            if isinstance(q, bool) or not isinstance(q, int) or q <= 0:
                raise ValueError('交接数量必须为正整数')
            s, n = t['satellite'], t['event']
            a, b = t['sender_vehicle'], t['receiver_vehicle']
            if n not in self.tasks or self.sat[n] != s or a == b:
                raise ValueError('交接车辆/事件不合法')
            if any(not isinstance(k, int) or k < 0 or k >= len(state['e2_routes'].get(s, []))
                   for k in (a, b)):
                raise ValueError('交接车辆不存在')
            sent[s, a, n] += q
            received[s, b, n] += q
        return sent, received

    def profile(self, route, sat, vehicle, sent, received):
        """Return arc loads, including incoming native inventory at each event."""
        w = sum(self.tasks[n].w for n in route[1:-1]
                if n in self.tasks and self.tasks[n].kind == 'd')
        v = sum(self.tasks[n].v for n in route[1:-1]
                if n in self.tasks and self.tasks[n].kind == 'd')
        own = relay = 0
        rows = [{'weight': w, 'volume': v, 'own': 0, 'relay': 0}]
        carried_ok = True
        for n in route[1:-1]:
            event = event_id(n)
            q = sent.get((sat, vehicle, event), 0)
            incoming_own = own
            carried_ok &= q <= incoming_own
            if n in self.tasks:
                task = self.tasks[n]
                if task.kind == 'd':
                    w -= task.w
                    v -= task.v
                else:
                    own += self.boxes[n]
            own -= q
            relay += received.get((sat, vehicle, event), 0)
            rows.append({'weight': w + self.cw * (own + relay),
                         'volume': v + self.cv * (own + relay),
                         'own': own, 'relay': relay, 'own_in': incoming_own})
        feasible = carried_ok and all(
            -TOL <= r['weight'] <= self.problem.e2_weight_cap + TOL
            and -TOL <= r['volume'] <= self.problem.e2_volume_cap + TOL
            and r['own'] >= 0 for r in rows)
        return rows, feasible, carried_ok

    def audit(self, state):
        """Validate all tasks, delivery commitments, arc loads and joint timing."""
        routes = state['e2_routes']
        if set(routes) != self.satellites:
            raise ValueError('卫星仓集合变化')
        sent, received = self.transfer_maps(state)
        served, visits, profiles = Counter(), {}, {}
        lower, upper, edges, task_visit = {}, {}, [], {}
        total_distance = 0.0

        def add_route(route, key, task_map, is_e1=False):
            nonlocal total_distance
            start, end = ('start', key), ('end', key)
            lower[start] = lower[end] = 0.0
            if is_e1:
                upper[start] = 0.0
            prev, prev_phys, service = start, route[0], 0.0
            for pos, n in enumerate(route[1:-1], 1):
                vkey = ('visit', key, pos)
                task = task_map.get(n)
                lower[vkey] = max(0.0, task.tw[0]) if task else 0.0
                upper[vkey] = task.tw[1] if task else math.inf
                if is_e1 and task.kind == 'p' and self.tfp < 9000:
                    lower[vkey] = max(lower[vkey], self.tfp)
                if task:
                    task_visit[n] = vkey
                if not is_e1:
                    visits[key[0], key[1], event_id(n)] = vkey
                edges.append((prev, vkey, service + self.travel(prev_phys, n)))
                total_distance += self.distance(prev_phys, n)
                prev, prev_phys, service = vkey, n, self.service(n)
            edges.append((prev, end, service + self.travel(prev_phys, route[-1])))
            total_distance += self.distance(prev_phys, route[-1])
            return start, end

        e1_routes = state['e1_routes']
        if len(e1_routes) > self.problem.e1_vehicle_count:
            raise ValueError('E1 车辆数超限')
        if Counter(n for r in e1_routes for n in r[1:-1]) != Counter(self.e1.keys()):
            raise ValueError('E1 必须恰好服务全部任务一次')
        e1_profiles = {}
        for k, route in enumerate(e1_routes):
            if len(route) < 2 or route[0] != self.problem.depot.id or route[-1] != self.problem.depot.id:
                raise ValueError('E1 路线未返回中心仓')
            w = sum(self.e1[n].w for n in route[1:-1] if self.e1[n].kind == 'd')
            v = sum(self.e1[n].v for n in route[1:-1] if self.e1[n].kind == 'd')
            rows = [{'weight': w, 'volume': v}]
            for n in route[1:-1]:
                t = self.e1[n]
                sign = -1 if t.kind == 'd' else 1
                w, v = w + sign * t.w, v + sign * t.v
                rows.append({'weight': w, 'volume': v})
            if any(x['weight'] < -TOL or x['volume'] < -TOL or
                   x['weight'] > self.problem.e1_weight_cap + TOL or
                   x['volume'] > self.problem.e1_volume_cap + TOL for x in rows):
                raise ValueError('E1 容量超限')
            e1_profiles[str(k)] = rows
            if len(route) > 2:
                add_route(route, ('e1', k), self.e1, True)
        for n, task in self.e1.items():
            d = task.cust + 'd'
            if task.kind == 'p' and d in task_visit:
                edges.append((task_visit[d], task_visit[n], self.service(d)))
        for s, paths in routes.items():
            if len(paths) > self.vehicles_per_satellite:
                raise ValueError('E2 车辆数超限')
            for k, route in enumerate(paths):
                if len(route) < 2 or route[0] != s or route[-1] != s:
                    raise ValueError('路线未返回所属卫星仓')
                nodes = [event_id(n) for n in route[1:-1]]
                if len(set(nodes)) != len(nodes) or any(self.sat.get(n) != s for n in nodes):
                    raise ValueError('事件重复或跨仓')
                for n in route[1:-1]:
                    if n in self.tasks:
                        served[n] += 1
                    elif not (sent.get((s, k, event_id(n)), 0) or received.get((s, k, event_id(n)), 0)):
                        raise ValueError('无交接目的的额外访问')
                profile, valid, _ = self.profile(route, s, k, sent, received)
                if not valid:
                    raise ValueError('容量超限或转出并非已携带的原生空箱')
                profiles[s, k] = profile
                if len(route) == 2:
                    continue
                start, end = add_route(route, (s, k), self.tasks)
                if s + 'd' in task_visit:
                    edges.append((task_visit[s + 'd'], start, self.service(s + 'd')))
                if s + 'p' in task_visit:
                    edges.append((end, task_visit[s + 'p'], 0.0))
        if served != Counter(self.tasks.keys()):
            raise ValueError('必须恰好服务全部原始任务一次')
        for n, task in self.tasks.items():
            if task.kind == 'p' and task.cust + 'd' in task_visit:
                d = task.cust + 'd'
                edges.append((task_visit[d], task_visit[n], self.service(d)))
        for t in state['transfers']:
            s, n = t['satellite'], t['event']
            left = visits.get((s, t['sender_vehicle'], n))
            right = visits.get((s, t['receiver_vehicle'], n))
            if left is None or right is None:
                raise ValueError('交接双方未访问同一事件')
            edges.append((left, right, self.service(n)))
        for _ in range(len(lower) + 1):
            changed = False
            for a, b, elapsed in edges:
                value = lower[a] + elapsed
                if value > lower[b] + 1e-7:
                    lower[b] = value
                    changed = True
            if any(t > upper.get(n, math.inf) + TOL for n, t in lower.items()):
                raise ValueError('时间窗或两级衔接不可行')
            if not changed:
                break
        else:
            raise ValueError('交接产生不可行的时间依赖环')
        transfers = []
        for t in state['transfers']:
            s, n = t['satellite'], t['event']
            ta = lower[visits[s, t['sender_vehicle'], n]]
            tb = lower[visits[s, t['receiver_vehicle'], n]]
            transfers.append({**t, 'physical_location': self.phys(n),
                              'sender_arrival_time': ta,
                              'handoff_ready_time': ta + self.service(n),
                              'receiver_pickup_time': tb})
        return {'objective': total_distance, 'transshipments': transfers,
                'handoff_batches': handoff_batches(transfers),
                'e1_load_profiles': e1_profiles,
                'load_profiles': {f'{s}:{k}': rows for (s, k), rows in profiles.items()},
                'service_times': {n: lower[v] for n, v in task_visit.items()},
                'route_return_times': {f'{s}:{k}': lower.get(('end', (s, k)), 0.0)
                                       for s, paths in routes.items() for k in range(len(paths))}}


def initial_info(allow_transfers, algorithm):
    return {'model': 8, 'version': 'joint_capacity_cooperation_v3',
            'formulation': 'joint_e1_e2_delivery_pickup_homogeneous_handoff',
            'proof_scope': 'full_joint_fixed_satellite_problem',
            'algorithm': algorithm, 'allow_transfers': allow_transfers,
            'proven_optimal': False, 'best_bound': None, 'mip_gap': None,
            'status': 'NO_SOLUTION', 'objective': None, 'feasible': False,
            'solution_type': 'no_solution', 'transshipment_count': 0,
            'transshipments': [], 'handoff_batches': [],
            'improvement_scope': 'joint_route_and_handoff_optimization_not_causal_relay_gain'}


def record_solution(info, state, audit):
    batches = audit['handoff_batches']
    info.update(objective=audit['objective'], feasible=True,
                transshipments=audit['transshipments'], handoff_batches=batches,
                transshipment_count=len(state['transfers']),
                transferred_boxes=sum(t['quantity'] for t in state['transfers']),
                transferred_container_count=sum(t['quantity'] for t in state['transfers']),
                handoff_deposit_count=len(batches),
                handoff_collection_visit_count=len({(t['satellite'], t['event'], t['receiver_vehicle']) for t in state['transfers']}),
                split_handoff_deposit_count=sum(len(b['collections']) > 1 for b in batches),
                state=deepcopy(state), independent_audit=audit,
                raw_e2_routes=deepcopy(state['e2_routes']),
                route_vehicle_indices={s: [k for k,r in enumerate(paths) if len(r)>2]
                                       for s,paths in state['e2_routes'].items()})


def export_routes(state):
    return ([r for r in state['e1_routes'] if len(r) > 2],
            {s: [r for r in paths if len(r) > 2] for s, paths in state['e2_routes'].items()})
