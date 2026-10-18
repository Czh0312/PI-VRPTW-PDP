"""Certificates, frozen study execution, and honest pool/comparison reporting."""
from copy import deepcopy
from dataclasses import replace
import csv
import hashlib
import json
from pathlib import Path
import random
import subprocess

import pytest

from tools.configuration.loader import load_routing_case, resolve_project_path
from experiments.analysis.summarize_static_multibatch import pool_evidence, paired_comparisons
from experiments.runs.run_multibatch_cycle import main as run_study, run_worker
from models.common.cycle import MultiBatchInstance, load_instance
from models.routing.model_5.alns.search.engine import solve_alns
from models.common.cycle.audit import audit_solution
from models.routing.model_5.alns.construction import chronological_state
from models.routing.model_5.alns.evaluator import evaluate, initial_state
from models.common.cycle.precheck import check_necessary_conditions
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tools.data.static_batch_dataset import load_manifest

MANIFEST = 'data/manifest.json'


def test_pool_bound_accounts_for_unavoidable_late_recovery():
    cfg, fleet = load_routing_case('config/experiments/examples/multibatch_demo.json')
    fleet['operating_policy']['container_pool_size'] = 2
    result = solve_alns(load_instance(cfg, fleet), max_time=.2)
    assert result['info']['status'] == 'INFEASIBLE'
    assert result['info']['proven_infeasible']
    assert result['info']['infeasibility_source'] == 'necessary_conditions'
    assert result['info']['objective'] is None
    assert result['precheck']['pool_lower_bound'] == 3
    assert result['precheck']['stock_bounds'][1]['optimistic_clean_returns'] == 1


@pytest.mark.parametrize('cleaning,expected', [(20, 1), (21, 2)])
def test_pool_bound_includes_exact_cleaning_boundary(cleaning, expected):
    check = check_necessary_conditions(fixture(pool=2, cleaning=cleaning))
    assert check['pool_lower_bound'] == expected


def test_final_cutoff_is_not_extended_by_cycle_tail():
    problem = fixture(one_batch=True)
    problem = MultiBatchInstance(problem.network, [replace(problem.orders[0], pickup_window=(31, 40))],
                                 problem.operating_policy)
    check = check_necessary_conditions(problem)
    assert check['proven_infeasible']
    assert 'FINAL_CUTOFF_UNREACHABLE' in {e['code'] for e in check['errors']}


def test_nonmetric_travel_uses_relaxed_shortest_paths():
    problem = fixture()
    # The direct D->S arc is long, but D->C->S is short. Ignoring node
    # eligibility for a lower bound is safe; assuming direct travel is not.
    problem._travel['D', 'S'] = 1000
    check = check_necessary_conditions(problem)
    assert check['passed'], check['errors']
    assert check['order_bounds'][0]['earliest_delivery'] == 13


@pytest.mark.parametrize('single,zero,pool', [(False, False, 1), (True, False, 1), (False, True, 1), (False, False, 5)])
def test_necessary_bound_never_rejects_valid_hand_schedule(single, zero, pool):
    problem = fixture(one_batch=single, zero=zero, pool=pool)
    result = hand_solution(problem)
    assert audit_solution(problem, result)['valid']
    check = check_necessary_conditions(problem)
    assert check['passed']
    assert check['pool_lower_bound'] <= audit_solution(problem, result)['minimum_pool_for_schedule']


def test_exact_precheck_needs_no_gurobi(monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith('gurobipy'):
            raise AssertionError('An analytic rejection must not load Gurobi')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    from models.routing.model_5.exact.search.engine import solve_exact
    result = solve_exact(fixture(pool=1, count=2))
    assert result['info']['proven_infeasible']
    assert not result['info']['proven_optimal']


def test_satellite_service_time_is_used_by_both_decoder_and_audit(tmp_path):
    cfg, fleet = load_routing_case('config/experiments/examples/multibatch_demo.json')
    with resolve_project_path(cfg['data']).open() as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        if row['node_type'] == 's':
            row['service_time'] = '1.5'
    path = tmp_path/'network.csv'
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    problem = load_instance({**cfg, 'data': str(path)}, fleet)
    assert all(problem.service(site) == 90 for site in problem.satellites)
    _, result, issues = evaluate(problem, initial_state(problem, random.Random(42)))
    assert not issues
    assert audit_solution(problem, result)['valid']
    changed = deepcopy(result)
    event = changed['e1_trips'][0]['events'][0]
    event['start'] += 1
    # E2 was dispatched immediately after unloading; delaying E1 breaks the handoff.
    assert not audit_solution(problem, changed)['valid']


def test_long_single_batch_initialization_preserves_early_pickups():
    path = 'data/single_batch/small/mechanism_forced_late/case.json'
    cfg, fleet = load_routing_case(path)
    problem = load_instance(cfg, fleet)
    _, _, violations = evaluate(problem, initial_state(problem, random.Random(42)))
    assert violations  # packed deliveries first miss the first cohort's pickup windows
    result = solve_alns(problem, max_time=1, max_iterations=0)
    assert result['info']['initial_strategy'] == 'chronological_paired_trips'
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert result['audit']['minimum_pool_for_schedule'] == 6


@pytest.mark.parametrize('family', ['mechanism_forced_late', 'mechanism_cutoff_choice'])
def test_all_paired_batch_variants_have_valid_starting_schedules(family):
    _, records = load_manifest(MANIFEST, family_ids=[family])
    for record in records:
        cfg, fleet = load_routing_case(record['case_path'])
        problem = load_instance(cfg, fleet)
        check = check_necessary_conditions(problem)
        _, result, issues = evaluate(problem, chronological_state(problem, random.Random(42)))
        audit = audit_solution(problem, result)
        assert not issues and audit['valid']
        assert check['passed'] and check['pool_lower_bound'] <= audit['minimum_pool_for_schedule']


@pytest.mark.parametrize('kind,size,customers', [
    ('zhou_derived_scenario', 'small', 7),
    ('zhou_derived_scenario', 'small', 10),
    ('zhou_derived_scenario', 'small', 12),
    ('zhou_derived_scenario', 'medium', 20),
    ('zhou_derived_scenario', 'medium', 30),
    ('zhou_derived_scenario', 'large', 50),
    ('zhou_derived_scenario', 'large', 100),
    ('lade_derived_scenario', 'small', 10),
    ('lade_derived_scenario', 'medium', 20),
    ('lade_derived_scenario', 'medium', 30),
])
def test_solver_accepts_representative_dataset_scales(kind, size, customers):
    _, records = load_manifest(MANIFEST, sizes=[size], batch_counts=[3], source_kinds=[kind])
    record = next(r for r in records if r['customer_count'] == customers)
    cfg, fleet = load_routing_case(record['case_path'])
    result = solve_alns(load_instance(cfg, fleet), max_time=2, max_iterations=0)
    assert result['audit']['valid'], result['info']
    assert result['audit']['periodic_replay']['valid']
    assert not result['info']['proven_optimal']


def test_manifest_hash_failure_prevents_any_runs(tmp_path):
    artifact = tmp_path/'input.json'
    artifact.write_text('original')
    manifest = dict(dataset_version='static_batch_suite_v1', information_mode='static_all_known',
                    assignment_mode='fixed_input', cases=[],
                    artifact_sha256={'input.json': hashlib.sha256(artifact.read_bytes()).hexdigest()})
    path = tmp_path/'manifest.json'
    path.write_text(json.dumps(manifest))
    artifact.write_text('changed')
    with pytest.raises(ValueError, match='DATASET_HASH_MISMATCH'):
        run_study(['--dataset-manifest', str(path), '--output', str(tmp_path/'out')])
    assert not (tmp_path/'out').exists()


def test_dry_run_freezes_job_matrix_without_creating_output(tmp_path):
    plan = run_study(['--dataset-manifest', MANIFEST, '--families', 'mechanism_forced_late',
                      '--batch-counts', '3', '--suggested-pools', '--solver', 'both',
                      '--cleaning-durations', '5', '10', '--seeds', '42', '43', '--dry-run',
                      '--output', str(tmp_path/'out')])
    assert plan['job_count'] == 18
    assert {j['pool'] for j in plan['jobs']} == {2, 4, 6}
    assert len({j['run'] for j in plan['jobs']}) == 18
    assert not (tmp_path/'out').exists()


def evidence_row(pool, *, valid=False, proven=False, lower=2, schedule=None, batch=3, cleaning=5):
    return dict(scenario_id='s', case_id='c', family_id='f', batch_count=batch,
                maximum_batch_demand=2, cleaning_duration=cleaning, pool=pool, valid=valid,
                proven_infeasible=proven, pool_lower_bound=lower,
                minimum_pool_for_schedule=schedule, solver='alns', seed=42,
                objective=50 if valid else None, status='FEASIBLE' if valid else 'HEURISTIC_NO_SOLUTION')


def test_pool_summary_does_not_turn_unknowns_into_proofs():
    rows = [evidence_row(2), evidence_row(3, valid=True, schedule=3)]
    summary = pool_evidence(rows)[0]
    assert summary['smallest_feasible_tested_pool'] == 3
    assert summary['smallest_pool_for_found_schedules'] == 3
    assert summary['proven_minimum_pool'] is None
    assert summary['unresolved_tested_pools'] == [2]
    rows[0]['proven_infeasible'] = True
    assert pool_evidence(rows)[0]['proven_minimum_pool'] == 3


def test_inconsistent_pool_evidence_is_flagged():
    rows = [evidence_row(2, valid=True, schedule=2), evidence_row(3, proven=True)]
    summary = pool_evidence(rows)[0]
    assert summary['evidence_conflict']
    assert summary['proven_minimum_pool'] is None


def test_batch_comparison_does_not_mix_pool_or_cleaning_scenarios():
    rows = [evidence_row(6, valid=True, batch=1), evidence_row(3, valid=True),
            evidence_row(6, valid=True), evidence_row(6, valid=True, cleaning=10)]
    compared = paired_comparisons(rows)
    assert sum(r['both_valid'] for r in compared) == 1
    assert sum(r['single_status'] == 'NOT_RUN' for r in compared) == 2


def test_worker_timeout_is_retained(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], .1, output=b'partial log')
    monkeypatch.setattr(subprocess, 'run', timeout)
    job = dict(run='timed', case_path='case.json', solver='alns', seed=42, budget=.1)
    row = run_worker(job, tmp_path, 1)
    assert row['status'] == 'OUTER_TIMEOUT' and not row['proven_infeasible']
    assert 'partial log' in (tmp_path/'timed.log').read_text()


def test_worker_malformed_output_is_retained_as_failure(tmp_path, monkeypatch):
    (tmp_path/'broken').mkdir()
    (tmp_path/'broken/result.json').write_text('{bad json')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout='done', stderr=''))
    row = run_worker(dict(run='broken', case_path='case.json', solver='alns', seed=42, budget=.1), tmp_path, 1)
    assert row['status'] == 'ERROR' and not row['valid'] and not row['proven_infeasible']
    assert 'JSONDecodeError' in row['error']


def test_frozen_worker_end_to_end(tmp_path):
    out = run_study(['--solver', 'alns', '--pool-sizes', '2', '3', '--seeds', '42',
                     '--time', '.2', '--iterations', '0', '--no-plots', '--output', str(tmp_path/'study')])
    manifest = json.loads((out/'manifest.json').read_text())
    assert manifest['job_count'] == 2
    for item in manifest['input_snapshots'].values():
        assert hashlib.sha256((out/item['snapshot']).read_bytes()).hexdigest() == item['sha256']
    with (out/'runs.csv').open() as handle:
        runs = list(csv.DictReader(handle))
    assert runs[0]['status'] == 'INFEASIBLE' and runs[0]['proven_infeasible'] == 'True'
    assert runs[1]['status'] == 'FEASIBLE' and runs[1]['periodic_replay'] == 'True'
    assert json.loads((out/'pool_summary.json').read_text())[0]['proven_minimum_pool'] == 3
    generated = json.loads(Path(manifest['jobs'][1]['case_path']).read_text())
    assert Path(generated['orders']).parent == out/'inputs'
    assert all((out/name).exists() for name in ('study_report.md', 'clean_stock.csv', 'cutoff_inventory.csv'))
    assert not (out/runs[1]['run']/'timeline.png').exists()
