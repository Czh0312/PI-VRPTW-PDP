"""Protect the formal study's denominator and cross-objective comparisons."""
import pytest

from experiments.runs.run_formal_benchmark27 import jobs_for, paired_rows


def record(model):
    return dict(base_case_id='S01', model=model, size='small', family_id='family',
                source_block='source', customer_count=7, case_path=f'm{model}.json')


def test_full_matrix_retains_each_method_seed_and_case():
    records = [{**record(m), 'base_case_id':f'C{n:02d}'} for n in range(27) for m in range(1,9)]
    jobs = jobs_for(records, 300, 30, [42,43,44,45,46])
    assert len(jobs)==1296
    assert sum(j['method']=='exact' for j in jobs)==216
    assert sum(j['method']=='alns' for j in jobs)==1080
    assert sum(j['budget'] for j in jobs)==97200
    assert len({j['id'] for j in jobs})==1296
    with pytest.raises(ValueError, match='distinct'):
        jobs_for(records, 300, 30, [42,42])


def test_paired_models_use_common_cost_across_native_objectives():
    common = dict(case='S01',method='alns',seed=42,size='small',source_block='source',valid=True,status='FEASIBLE')
    rows = [dict(common,model=5,objective=100,comparable_reusable_cost=500),
            dict(common,model=6,objective=400,comparable_reusable_cost=400)]
    pair, = paired_rows(rows)
    assert pair['metric']=='comparable_reusable_cost'
    assert pair['difference_right_minus_left']==-100
    assert 'both objective and batch rules' in pair['interpretation']


def test_failed_member_stays_in_pair_without_invented_improvement():
    common = dict(case='S01',method='exact',seed=42,size='small',source_block='source')
    rows = [dict(common,model=1,objective=100,valid=True,status='OPTIMAL'),
            dict(common,model=2,objective=None,valid=False,status='TIME_LIMIT')]
    pair, = paired_rows(rows)
    assert not pair['both_valid']
    assert pair['right_status']=='TIME_LIMIT'
    assert pair['difference_right_minus_left'] is None


def test_current_m5_comparisons_use_matching_costs_and_describe_added_decisions():
    common = dict(case='S01', method='alns', seed=42, size='small', source_block='source',
                  valid=True, status='FEASIBLE')
    rows = [dict(common, model=3, objective_mode='distance', objective=100, comparable_reusable_cost=500),
            dict(common, model=5, objective_mode='cost', objective=450, comparable_reusable_cost=450),
            dict(common, model=6, objective_mode='cost', objective=400, comparable_reusable_cost=400)]
    pairs = paired_rows(rows)
    assert all(pair['metric'] == 'comparable_reusable_cost' for pair in pairs)
    m56 = next(pair for pair in pairs if (pair['left'], pair['right']) == (5, 6))
    assert 'same cost objective' in m56['interpretation']
    assert m56['difference_right_minus_left'] == -50
