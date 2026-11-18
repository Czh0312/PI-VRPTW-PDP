"""Keep historical inputs usable without recreating legacy data directories."""
import hashlib
from pathlib import Path

from tools.configuration import loader
from models.common.data_loader import parse_dataset


def test_legacy_paths_resolve_but_new_outputs_and_external_paths_do_not(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    archive = project / 'archive/datasets/legacy_20261015/data'
    legacy = archive / 'shared/example.json'
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"archived": true}')
    monkeypatch.setattr(loader, 'PROJECT_ROOT', project)
    monkeypatch.setattr(loader, 'LEGACY_DATA_ROOT', archive)
    assert loader.load_json('data/shared/example.json') == {'archived': True}
    assert loader.resolve_project_path(project / 'data/shared/example.json') == legacy
    assert loader.resolve_project_path('data/shared') == legacy.parent
    assert loader.resolve_project_path('data/shared/new.json') == project / 'data/shared/new.json'
    external = tmp_path / 'external/data/shared/example.json'
    assert loader.resolve_project_path(external) == external
    current = project / 'data/shared/example.json'
    current.parent.mkdir(parents=True)
    current.write_text('{"current": true}')
    assert loader.load_json('data/shared/example.json') == {'current': True}


def test_archived_csv_works_through_public_parser():
    name = 'data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv'
    assert not (loader.PROJECT_ROOT / name).exists()
    assert loader.resolve_project_path(name).is_relative_to(loader.LEGACY_DATA_ROOT)
    old_nodes, old_fleet = parse_dataset(name)
    new_nodes, new_fleet = parse_dataset(str(loader.resolve_project_path(name)))
    assert old_fleet == new_fleet
    assert {key: vars(node) for key, node in old_nodes.items()} == {
        key: vars(node) for key, node in new_nodes.items()}


def test_basetest_rename_preserves_previous_case_paths():
    assert loader.resolve_project_path('data') == loader.PROJECT_ROOT / 'data'
    assert loader.resolve_dataset_root('data') == loader.BENCHMARK_ROOT
    for size, case_id in (('small', 'S01'), ('medium', 'M01'), ('large', 'L01')):
        table = loader.BENCHMARK_ROOT / size / f'{case_id}.csv'
        old_table = Path('data') / size / table.name
        case, fleet = loader.load_routing_case(case_id, model=3)
        assert case['dataset_version'] == 'basetest'
        assert case['data'] == str(table)
        for reference in (table, old_table, loader.PROJECT_ROOT / old_table):
            assert loader.load_routing_case(reference, model=3) == (case, fleet)
        assert loader.resolve_project_path(old_table.with_name('config.json')) == table.with_name('config.json')


def test_archived_root_config_keeps_most_recent_preset_at_original_path():
    reference = Path('config/experiments/examples/single_batch_demo.json')
    archived = loader.ROOT_CONFIG_ARCHIVE / reference.relative_to('config')
    assert loader.resolve_project_path(reference) == archived
    assert loader.resolve_project_path(loader.PROJECT_ROOT / reference) == archived
    assert loader.load_json(reference)['benchmark_case'] == 'S01'


def test_original_benchmark_fee_paths_use_the_frozen_profile():
    expected = '051d379bc656647b0f3c9e88063160e8bc2f5affce8b72e639104a6df1eeb0d2'
    for size, case in (('small', 'S01'), ('medium', 'M01'), ('large', 'L01')):
        old_path = f'data/benchmark27_models_1_6_v1/shared/{size}/{case}/economic.json'
        resolved = loader.resolve_project_path(old_path)
        assert resolved == loader.FROZEN_BENCHMARK_ROOT / 'shared' / size / case / 'economic.json'
        assert hashlib.sha256(resolved.read_bytes()).hexdigest() == expected


def test_csv_parser_preserves_paths_relative_to_callers_directory(tmp_path, monkeypatch):
    (tmp_path / 'local.csv').write_text('node_id,node_type,x,y\nD0,d,0,0\n')
    monkeypatch.chdir(tmp_path)
    nodes, _ = parse_dataset('local.csv')
    assert set(nodes) == {'D0'}


def test_historical_generators_are_archived():
    history = loader.PROJECT_ROOT/'archive/experiments/pre_renumbering_20261113/tools/data'
    assert (history/'build_static_batch_suite.py').is_file()
    assert (history/'build_model6_uniform_dataset.py').is_file()


def test_retired_cases_resolve_without_recreating_active_folders(tmp_path,monkeypatch):
    project=tmp_path/'project'
    retired=project/'archive/datasets/retired_20261024'
    monkeypatch.setattr(loader,'PROJECT_ROOT',project)
    monkeypatch.setattr(loader,'RETIRED_CASE_ROOT',retired)
    for relative in ('data/old_dataset/case.json','config/experiments/old_case/case.json'):
        path=retired/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{"archived":true}')
        assert loader.load_json(relative)=={'archived':True}
        assert loader.resolve_project_path(relative)==path
        assert not (project/relative).exists()
    assert loader.resolve_project_path('data/new_output.json')==project/'data/new_output.json'
