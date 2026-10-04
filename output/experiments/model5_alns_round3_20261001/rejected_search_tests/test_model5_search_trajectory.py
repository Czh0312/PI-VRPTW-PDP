"""Longer M5 budgets continue the prefix and cannot suppress stalled restarts."""
from copy import deepcopy
from itertools import permutations

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.domain.instance import ProblemInstance
from models.routing.solvers.alns import solver
from models.routing.solvers.alns.cycle_cache import CycleEvaluator
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.intensification import CycleDescent
from models.routing.solvers.alns.neighborhoods import clone_state, state_key
from tests.routing.test_model5_intensification import directed_cycle


def test_time_and_iteration_limits_do_not_change_an_uninterrupted_search_prefix(monkeypatch):
    problem, _ = directed_cycle()
    trace = []
    original = CycleEvaluator.__call__
    def recording(self, problem, state):
        trace.append(state_key(state))
        return original(self,problem,state)
    monkeypatch.setattr(CycleEvaluator,'__call__',recording)
    shorter = solver.solve_alns(problem,max_time=10,max_iterations=1100,seed=47)
    prefix = list(trace)
    trace.clear()
    longer = solver.solve_alns(problem,max_time=20,max_iterations=2300,seed=47)
    assert trace[:len(prefix)] == prefix
    assert longer['info']['objective'] <= shorter['info']['objective']+1e-9
    assert shorter['info']['shared_search_iterations'] == longer['info']['shared_search_iterations'] == 600
    assert longer['audit']['valid'] and longer['audit']['periodic_replay']['valid']


def test_stagnation_restart_has_priority_over_nonempty_descent(monkeypatch):
    problem, _ = directed_cycle()
    modes = []
    monkeypatch.setattr(solver.random.Random,'choices',lambda self,*args,**kwargs:[0])
    monkeypatch.setattr(solver,'_move',lambda problem,state,rng,op:clone_state(state))
    def repair(problem,state,rng,mode,deadline):
        modes.append(mode)
        return clone_state(state)
    monkeypatch.setattr(solver,'destroy_repair',repair)
    def no_improvement(self,state,decoded,deadline):
        proposal = clone_state(state)
        hints = proposal['pickup_vehicle']['b1']
        proposal['pickup_vehicle']['b1'] = {} if hints else {'S':0}
        return proposal
    monkeypatch.setattr(CycleDescent,'propose',no_improvement)
    result = solver.solve_alns(problem,max_time=10,max_iterations=2100)
    info = result['info']
    assert modes == ['related','random']
    assert info['incumbent_restarts'] == 2
    assert info['operator_attempts']['restart_related'] == info['operator_attempts']['restart_random'] == 1
    assert info['operator_attempts']['cycle_descent'] > 0
    assert info['descent_local_improvements'] == 0
    assert result['audit']['valid']


def test_descent_can_improve_a_worse_feasible_branch_before_beating_incumbent(monkeypatch):
    base, _ = directed_cycle()
    distances = {a:{b:0 if a==b else 1+.0001*base.distance(a,b)
                   for b in base.network.nodes} for a in base.network.nodes}
    fleet = dict(E1_vehicle_count=1,vehicles_per_satellite=2,E1_Qk=20,E1_Ck=20,
                 E2_Qv=10,E2_Cv=10,container_weight=1,container_volume=1)
    durations = {a:{b:int(a!=b) for b in base.network.nodes} for a in base.network.nodes}
    problem = MultiBatchInstance(ProblemInstance(base.network.nodes,fleet,
        distance_matrix=distances,travel_time_matrix=durations),base.orders,base.operating_policy,model=5)
    states = []
    for deliveries in permutations('ABC'):
        for pickups in permutations('ABC'):
            state = dict(deliveries={b:[['S']] for b in ('b1','b2')},
                pickup_order={b:['S'] for b in ('b1','b2')},
                pickup_vehicle={b:{} for b in ('b1','b2')},
                e2=[dict(satellite='S',vehicle=0,
                         tasks=[(f'{b}_{c}','d') for c in deliveries]+[(f'{b}_{c}','p') for c in pickups])
                    for b in ('b1','b2')])
            score,decoded,issues = evaluate(problem,state)
            if not issues and all(abs(score-old[0])>1e-8 for old in states):
                states.append((score,state,decoded))
    states.sort(key=lambda x:x[0])
    assert len(states)>=4
    # A is the incumbent. A random B move is a small worsening; descent B→C
    # is useful even though C does not beat A yet. Its next move C→D does.
    d,a,c,b = states[:4]
    calls, moved = [], False
    monkeypatch.setattr(solver,'initial_state',lambda problem,rng:clone_state(a[1]))
    def move(problem,state,rng,op):
        nonlocal moved
        if not moved:
            moved = True
            return clone_state(b[1])
        return clone_state(state)
    monkeypatch.setattr(solver,'_move',move)
    monkeypatch.setattr(solver,'destroy_repair',lambda problem,state,rng,mode,deadline:move(problem,state,rng,mode))
    def descent(self,state,decoded,deadline):
        calls.append(state_key(state))
        if state_key(state) == state_key(b[1]):return clone_state(c[1])
        if state_key(state) == state_key(c[1]):return clone_state(d[1])
        return None
    monkeypatch.setattr(CycleDescent,'propose',descent)
    warm = deepcopy(a[2])
    result = solver.solve_alns(problem,max_time=10,max_iterations=8,initial_result=warm)
    assert warm == a[2]
    assert state_key(b[1]) in calls and state_key(c[1]) in calls
    assert result['info']['objective'] == pytest.approx(d[0])
    assert result['info']['descent_local_improvements'] == 2
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
