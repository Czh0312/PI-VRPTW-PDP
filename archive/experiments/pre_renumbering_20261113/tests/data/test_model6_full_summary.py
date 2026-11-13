"""Full-suite statistics retain failures and count unfavorable comparisons."""
import json

import pytest

from experiments.analysis.summarize_model6_resources import summarize
from experiments.analysis.summarize_model6_full_suite import comparison_statistics,observed_resource_frontier


def case(pool,batches,*,complete=True):
    if not complete:
        return {'comparison_complete':False}
    return dict(comparison_complete=True,m5_smallest_required_pool_among_runs=10,m6_pool=pool,
                m5_delivery_batches=3,m6_delivery_batches=batches,m5_best_distance=100,m6_distance=110,
                m5_e1_trips=3,m6_e1_trips=4,m5_e2_trips=5,m6_e2_trips=4,
                resource_certificate=dict(pool_minimum_proven=False,
                    delivery_batch_minimum_given_pool_proven=True,cost_optimal_proven=False),
                free_assignment_improved_cost_over_fixed=False)


def test_statistics_keep_equal_worse_and_missing_cases():
    result=comparison_statistics([case(8,2),case(10,3),case(12,4),case(None,None,complete=False)])
    assert result['planned_cases']==4 and result['compared_cases']==3 and result['missing_cases']==1
    assert (result['pool_reduced'],result['pool_equal'],result['pool_increased'])==(1,1,1)
    assert (result['batches_reduced'],result['batches_equal'],result['batches_increased'])==(1,1,1)
    assert result['mean_boxes_saved']==pytest.approx(0)
    assert result['mean_pool_reduction_percent']==pytest.approx(0)
    assert result['mean_distance_reduction_percent']==pytest.approx(-10)
    assert result['mean_e1_trips_saved']==-1
    assert result['fixed_batching_comparable']==3 and result['free_batching_cost_better_than_fixed']==0
    assert comparison_statistics([case(None,None,complete=False)])['mean_boxes_saved'] is None


def test_resource_report_retains_missing_comparators(tmp_path):
    (tmp_path/'inputs').mkdir()
    record=dict(name='timed_out_case',orders=100,total_container_uses=200,source_kind='zhou_derived_scenario',
                size='large',family_id='one_family',candidate_batch_count=3)
    (tmp_path/'inputs/manifest.json').write_text(json.dumps({'cases':[record]}))
    (tmp_path/'experiment.json').write_text(json.dumps({'jobs':[]}))
    row=dict(run='failed',case='timed_out_case',treatment='M6_joint',valid=False,status='TIME_LIMIT_BUILD',error='')
    (tmp_path/'rows.json').write_text(json.dumps([row]))
    result=summarize(tmp_path)
    assert result['planned_cases']==1 and result['complete_comparison_cases']==0
    assert result['incomplete_comparison_cases']==1 and result['valid_runs']==0
    assert result['cases'][0]['comparison_complete'] is False
    assert result['cases'][0]['missing_feasible_treatments']==['M5_distance','M6_joint']
    assert result['no_incumbent_runs']==[{'run':'failed','status':'TIME_LIMIT_BUILD'}]
    assert 'timed_out_case' in (tmp_path/'resources.md').read_text()


def test_observed_frontier_keeps_box_batch_tradeoffs_and_witnesses():
    rows=[dict(pool=n,batches=b,cost=cost,run=run,treatment=treatment,valid=valid)
          for n,b,cost,run,treatment,valid in [(5,3,80,'fixed','M6_pool_only',True),
                    (8,3,70,'dominated','M6_joint',True),(11,2,60,'joint','M6_joint',True),
                    (11,2,65,'duplicate','M6_joint',True),(1,1,1,'failed','M6_joint',False),
                    (1,1,1,'baseline','M5_distance',True)]]
    result=observed_resource_frontier(rows)
    assert [(r['pool'],r['delivery_batches']) for r in result]==[(5,3),(11,2)]
    assert [r['witness_run'] for r in result]==['fixed','joint']
