from experiments.analysis.report_thesis_ablation import common_config, source_family, summarize


def test_summary_keeps_failed_pairs_and_normalizes_each_pair():
    result = summarize([{'A_minus_D': 10, 'A_upper': 100},
                        {'A_minus_D': -10, 'A_upper': 200},
                        {'A_minus_D': None, 'A_upper': None}], 'A_minus_D')
    assert result['planned'] == 3 and result['valid'] == 2
    assert result['positive'] == result['negative'] == 1
    assert result['mean_pct_of_A'] == 2.5


def test_matching_uses_matrix_contents_and_keeps_calendar(tmp_path):
    first, second = tmp_path/'first.csv', tmp_path/'second.csv'
    first.write_text('1,2\n')
    second.write_text('1,2\n')
    a = dict(distance_matrix=str(first), duration_matrix=str(first),
             joint_batch_options=dict(fixed_assignment={'o': 'b1'}, pool_min=3, pool_max=3,
                                      cutoff_bounds_minutes={'b1': [2, 2]}))
    b = dict(distance_matrix=str(second), duration_matrix=str(second),
             joint_batch_options=dict(pool_min=1, pool_max=3, cutoff_bounds_minutes={'b1': [2, 2]}))
    assert common_config(a) == common_config(b)
    second.write_text('1,3\n')
    assert common_config(a) != common_config(b)
    second.write_text('1,2\n')
    b['joint_batch_options']['cutoff_bounds_minutes'] = {'b1': [3, 3]}
    assert common_config(a) != common_config(b)
    assert a['joint_batch_options']['fixed_assignment'] == {'o': 'b1'}


def test_lade_related_selections_share_source_block():
    meta = dict(source_kind='lade_derived_scenario', spatial_block='lade:hangzhou_0823_0829')
    assert source_family('lade_20', meta) == source_family('lade_30', meta)
    assert source_family('zhou_1', {}) == 'zhou_1'
