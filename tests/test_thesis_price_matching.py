from copy import deepcopy

import pytest

from experiments.analysis.thesis_price_matching import validate_price_design, validate_price_results
from experiments.runs.run_thesis_completion import write


def design(tmp_path):
    rows = []
    for index, factor in enumerate((.5, 1., 2.)):
        matrix = tmp_path/f'matrix{index}.json'
        write(matrix, [[0, 1], [1, 0]])
        write(tmp_path/f'case{index}.json', dict(distance_matrix=str(matrix),
            operating_policy={'cleaning_duration': 5}, joint_batch_options={'pool_min': 1},
            economic_config=dict(container_pool_cost_per_period=10*factor,
                description=f'Scenario {factor}', e1_work_cost_per_minute=2)))
        rows.append(dict(id=str(index), base_case='x', model=6, seed=42, price_factor=factor,
            case_path=f'case{index}.json', counts=[1, 2, 3], method='alns', budget=300,
            iterations=1000, version='final', status='NOT_RUN', valid=False))
    return rows


def test_equal_content_different_paths_and_unexecuted_design(tmp_path):
    rows = design(tmp_path)
    evidence = validate_price_design(tmp_path, rows)
    assert len(evidence) == 3 and all(r['matched'] for r in evidence)
    assert {r['baseline_id'] for r in evidence} == {'1'}


@pytest.mark.parametrize('change', ['matrix', 'policy', 'restriction', 'rate', 'other_price', 'budget', 'counts'])
def test_reject_confounded_comparison(tmp_path, change):
    import json
    rows = design(tmp_path)
    path = tmp_path/'case0.json'
    config = json.loads(path.read_text(encoding='utf-8'))
    if change == 'matrix':
        write(tmp_path/'matrix0.json', [[0, 2], [2, 0]])
    elif change == 'policy':
        config['operating_policy']['cleaning_duration'] = 6
    elif change == 'restriction':
        config['joint_batch_options']['pool_min'] = 2
    elif change == 'rate':
        config['economic_config']['container_pool_cost_per_period'] = 6
    elif change == 'other_price':
        config['economic_config']['e1_work_cost_per_minute'] = 3
    elif change == 'budget':
        rows[0]['budget'] = 60
    else:
        rows[0]['counts'] = [1, 2]
    write(path, config)
    with pytest.raises(ValueError):
        validate_price_design(tmp_path, rows)


def test_missing_baseline_or_duplicate_fails(tmp_path):
    rows = design(tmp_path)
    with pytest.raises(ValueError, match='all three'):
        validate_price_design(tmp_path, [r for r in rows if r['price_factor'] != 1.])
    with pytest.raises(ValueError, match='Duplicate'):
        validate_price_design(tmp_path, rows+[deepcopy(rows[0])])


def test_actual_iterations_can_differ_but_registered_budget_cannot(tmp_path):
    rows = design(tmp_path)
    write(tmp_path/'manifest.json', dict(jobs=rows))
    observed = deepcopy(rows)
    for i, row in enumerate(observed):
        row['iterations'] = 100+i
    assert len(validate_price_results(tmp_path, observed)) == 3
    observed[0]['budget'] = 60
    with pytest.raises(ValueError, match='metadata'):
        validate_price_results(tmp_path, observed)
