"""Coverage, honest references and observational log parsing for long runs."""
from collections import Counter

import pytest

from experiments.runs.run_spacetime_quality import (
    make_jobs, conditional_skip, reference_metrics, parse_gurobi_log, rental_profile,
    process_family_memory,
)


def records():
    return [dict(base_case_id=f'{prefix}{i:02}', model=m, size=size,
                 family_id='f', source_block='b', customer_count=7)
            for prefix, size in (('S','small'), ('M','medium'), ('L','large'))
            for i in range(1,10) for m in range(1,9)]


def test_complete_matrix_and_conditional_budgets():
    jobs = make_jobs(records())
    assert len(jobs) == 2880
    assert len({j['id'] for j in jobs}) == len(jobs)
    exact = [j for j in jobs if j['method'] == 'exact']
    assert Counter(j['budget'] for j in exact) == {300:72, 3600:72, 10800:72, 600:72}
    assert not any(j['size']=='large' for j in exact)
    for budget in (10,60,300,900):
        selected = [j for j in jobs if j['method']=='alns' and j['budget']==budget]
        assert len(selected)==648
        assert Counter(j['seed'] for j in selected)=={42:216,43:216,44:216}
    observed = set()
    for j in jobs:
        assert not j['prerequisite'] or j['prerequisite'] in observed
        observed.add(j['id'])


@pytest.mark.parametrize('status,optimal,expected', [
    ('OPTIMAL', True, 'SKIPPED_PROVEN_OPTIMAL'),
    ('TIME_LIMIT', False, None),
    ('RESOURCE_LIMIT', False, 'SKIPPED_RESOURCE_LIMIT'),
    ('SKIPPED_PROVEN_OPTIMAL', False, 'SKIPPED_PROVEN_OPTIMAL'),
])
def test_exact_extension_preserves_proof_and_resource_status(status, optimal, expected):
    job = dict(method='exact', prerequisite='prior')
    prior = dict(status=status, proven_optimal=optimal, valid=optimal)
    assert conditional_skip(job, {'prior':prior}) == expected


def test_feasibility_retry_does_not_hide_primary_failure():
    job = dict(method='alns', prerequisite='prior')
    assert conditional_skip(job, {'prior':dict(valid=True)}) == 'SKIPPED_ALREADY_FEASIBLE'
    assert conditional_skip(job, {'prior':dict(valid=False)}) is None
    with pytest.raises(RuntimeError):
        conditional_skip(job, {})


def reference(status='TIME_LIMIT', optimal=False, objective=100, bound=90):
    return dict(id='ref',case='S01',model=1,status=status,valid=True,
                proven_optimal=optimal,objective=objective,best_bound=bound,solve_wall_seconds=50)


def test_unproven_reference_does_not_create_optimal_gap():
    row = dict(case='S01',model=1,valid=True,objective=95)
    metrics = reference_metrics(row, [reference()])
    assert metrics['optimal_gap_percent'] is None
    assert metrics['feasible_reference_difference_percent']==-5
    assert metrics['certified_gap_upper_percent']==pytest.approx(100*5/90)


def test_proven_reference_and_conflicting_certificate():
    row = dict(case='S01',model=1,valid=True,objective=110)
    metrics = reference_metrics(row, [reference('OPTIMAL',True,100,100)])
    assert metrics['optimal_gap_percent']==10
    conflict = reference_metrics({**row,'objective':99}, [reference('OPTIMAL',True,100,100)])
    assert conflict['reference_conflict']
    assert conflict['optimal_gap_percent'] is None


def test_failures_are_not_zero_gap_observations():
    row = dict(case='S01',model=1,valid=False,objective=None)
    metrics = reference_metrics(row, [reference('OPTIMAL',True,100,100)])
    assert metrics['optimal_gap_percent'] is None
    assert metrics['feasible_reference_difference_percent'] is None


def test_log_times_include_build_and_do_not_claim_independent_audit():
    log = 'Root relaxation: objective 2, 1 iterations, 0.01 seconds\n' \
          '     0     0  90.00000    0    4  100.00000  90.00000  10.0%     -    5s\n' \
          'H    0     0                     95.00000  91.00000  4.21%     -   10s\n'
    rows = parse_gurobi_log(log,2)
    assert [r['elapsed'] for r in rows]==[7,12]
    assert rows[-1]['objective']==95
    assert rows[-1]['best_bound']==91
    assert not rows[-1]['independently_audited']


def test_rental_one_is_used_by_cost_profile_without_purchase_investment():
    import json
    from pathlib import Path
    from models.cost.joint_batch import JointCostProfile
    source = Path(__file__).resolve().parents[2]/'data/formal_benchmark27_spacetime_v2/shared/small/S01/economic.json'
    original = json.loads(source.read_text())
    profile = rental_profile(original)
    assert original['container_pool_cost_per_period']==5
    assert profile['container_pool_cost_per_period']==1
    assert profile['pool_cost_basis']=='rental'
    assert profile['container_price']==0
    assert profile['cleaning_cost_per_return']==original['cleaning_cost_per_return']
    audit = dict(distances=dict(e1=100,e2=200),time_metrics=dict(working_minutes=dict(e1=0,e2=0)),
        e1_vehicle_count=1,e2_vehicle_count=1,e1_trip_count=1,e2_trip_count=1,
        container_balance=dict(returned_to_depot=7,issued=7))
    cost = JointCostProfile(profile).evaluate(audit,4)
    assert cost['components']['container_pool']==4
    assert cost['initial_pool_investment']==0
    assert cost['total']==pytest.approx(24.14)


def test_windows_launcher_memory_includes_interpreter_children():
    from types import SimpleNamespace
    class Process:
        def __init__(self,pid,rss,children=()):
            self.pid,self.rss,self.descendants=pid,rss,children
        def children(self,recursive):
            assert recursive
            return self.descendants
        def memory_info(self):
            return SimpleNamespace(rss=self.rss)
        def create_time(self):
            return self.pid*10
    child = Process(2,200)
    parent = Process(1,5,[child])
    memory, family = process_family_memory(parent)
    assert memory==205
    assert family==[dict(pid=1,created_at=10),dict(pid=2,created_at=20)]
