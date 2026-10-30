"""Keep historical inputs usable without recreating legacy data directories."""
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


def test_csv_parser_preserves_paths_relative_to_callers_directory(tmp_path, monkeypatch):
    (tmp_path / 'local.csv').write_text('node_id,node_type,x,y\nD0,d,0,0\n')
    monkeypatch.chdir(tmp_path)
    nodes, _ = parse_dataset('local.csv')
    assert set(nodes) == {'D0'}


def test_historical_generators_default_outside_formal_data():
    from tools.data import build_static_batch_suite, build_model6_uniform_dataset
    from tools.data import build_model5_model6_shared_dataset
    for module in (build_static_batch_suite, build_model6_uniform_dataset,
                   build_model5_model6_shared_dataset):
        target = Path(module.DEFAULT_OUTPUT)
        if not target.is_absolute():
            target = loader.PROJECT_ROOT / target
        assert target.is_relative_to(loader.PROJECT_ROOT / 'output/generated_datasets')


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
