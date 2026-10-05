"""Pool settings must reach the solver and survive frozen experiment execution."""
import hashlib
import json

import pytest

from configuration.container_pools import configured_pool, load_container_pools
from configuration.loader import load_json, load_routing_case, resolve_project_path
from experiments.runs.run_multibatch_cycle import main as run_study
from tools.data.static_batch_dataset import load_manifest

MANIFEST = 'data/manifest.json'
POOL_CONFIG = 'config/routing/container_pools.json'


def write_config(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return str(path)


def pool_file(tmp_path, ratio=.5):
    return write_config(tmp_path/'pools.json', dict(demand_ratio=ratio))


def test_case_override_preserves_source_policy(tmp_path, monkeypatch):
    from configuration import container_pools
    monkeypatch.setattr(container_pools, 'DEFAULT_POOL_CONFIG', pool_file(tmp_path, 1.0))
    case = load_json('config/cases/multibatch_demo.json')
    policy_path = resolve_project_path(case['operating_policy'])
    original = policy_path.read_bytes()
    case['container_pool_size'] = 2
    cfg, fleet = load_routing_case(write_config(tmp_path/'case.json', case))
    assert cfg['container_pool_size'] == fleet['operating_policy']['container_pool_size'] == 2
    assert policy_path.read_bytes() == original
    del case['container_pool_size']
    _, fleet = load_routing_case(write_config(tmp_path/'case.json', case))
    assert fleet['operating_policy']['container_pool_size'] == 4


@pytest.mark.parametrize('value', [0, -1, True, 3.5, 3.0, '3', None])
def test_case_rejects_invalid_pool(tmp_path, value):
    case = load_json('config/cases/multibatch_demo.json')
    case['container_pool_size'] = value
    with pytest.raises(ValueError, match='positive integer'):
        load_routing_case(write_config(tmp_path/'case.json', case))


def test_cli_override_wins_over_case(monkeypatch):
    from cli.route import main
    from cli import multibatch
    monkeypatch.setattr(multibatch, 'run_case', lambda config, fleet, **kw: (config, fleet))
    cfg, fleet = main(['--case', 'config/cases/multibatch_demo.json', '--container-pool-size', '4'])
    assert cfg['container_pool_size'] == fleet['operating_policy']['container_pool_size'] == 4


def test_shipped_config_covers_all_dataset_variants():
    manifest, _ = load_manifest(MANIFEST)
    pools = load_container_pools(POOL_CONFIG)
    assert set(pools) == {'demand_ratio'}
    for record in manifest['cases']:
        assert configured_pool(pools, record['total_container_uses']) > 0


def test_one_setting_applies_across_all_families(tmp_path):
    path = pool_file(tmp_path)
    plan = run_study(['--dataset-manifest', MANIFEST,
                      '--pool-config', path, '--solver', 'alns', '--seeds', '42', '--dry-run'])
    assert plan['job_count'] == 279
    for job in plan['jobs']:
        assert job['pool'] == (job['total_container_uses'] + 1) // 2
    small = next(j for j in plan['jobs'] if j['case_id'] == 'zhou_Set1_7-2-1_b1')
    small_record = next(r for r in load_manifest(MANIFEST)[1] if r['case_id']==small['case_id'])
    assert small['pool'] == (small_record['total_container_uses']+1)//2


@pytest.mark.parametrize('ratio', [0, -1, True, '0.5', None, float('inf'), float('nan')])
def test_bad_pool_config_stops_before_output(tmp_path, ratio):
    path = pool_file(tmp_path, ratio)
    with pytest.raises(ValueError, match='finite positive number'):
        run_study(['--dataset-manifest', MANIFEST, '--families', 'mechanism_forced_late',
                   '--pool-config', path, '--output', str(tmp_path/'out')])
    assert not (tmp_path/'out').exists()


def test_pool_config_is_snapshotted_and_used_by_worker(tmp_path):
    path = pool_file(tmp_path)
    digest = hashlib.sha256(resolve_project_path(path).read_bytes()).hexdigest()
    out = run_study(['--dataset-manifest', MANIFEST, '--families', 'mechanism_forced_late',
                     '--batch-counts', '3', '--pool-config', path, '--solver', 'alns',
                     '--seeds', '42', '--time', '.2', '--iterations', '0', '--no-plots',
                     '--output', str(tmp_path/'study')])
    manifest = load_json(out/'manifest.json')
    snapshot = manifest['input_snapshots'][path]
    assert snapshot['sha256'] == digest
    assert hashlib.sha256((out/snapshot['snapshot']).read_bytes()).hexdigest() == digest
    job = manifest['jobs'][0]
    effective = load_json(out/job['run']/'manifest.json')
    result = load_json(out/job['run']/'result.json')
    assert job['pool'] == effective['policy']['container_pool_size'] == 3
    assert effective['config']['container_pool_size'] == 3
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    # Changing the original settings cannot alter the already generated case.
    pool_file(tmp_path, 99)
    _, fleet = load_routing_case(job['case_path'])
    assert fleet['operating_policy']['container_pool_size'] == 3
    load_manifest(MANIFEST)  # original dataset hashes are intact


def test_sweep_overrides_top_level_case_pool(tmp_path):
    plan = run_study(['--case', 'config/cases/multibatch_demo.json', '--pool-sizes', '2', '4',
                      '--solver', 'alns', '--seeds', '42', '--dry-run'])
    assert [j['pool'] for j in plan['jobs']] == [2, 4]
    # A frozen sweep must not be replaced by the default shared ratio.
    from argparse import Namespace
    from experiments.runs.run_multibatch_cycle import prepare_variants
    args = Namespace(case=['config/cases/multibatch_demo.json'], pool_config=None,
                     suggested_pools=False, pool_sizes=[2, 4], cleaning_durations=None,
                     no_plots=True, iterations=0)
    for variant in prepare_variants(args, []):
        _, fleet = load_routing_case(write_config(tmp_path/'case.json', variant['config']))
        assert fleet['operating_policy']['container_pool_size'] == variant['pool']


def test_cli_and_batch_runner_use_shared_setting_by_default(tmp_path, monkeypatch):
    from cli import multibatch
    from cli.route import main
    from configuration import container_pools
    monkeypatch.setattr(container_pools, 'DEFAULT_POOL_CONFIG', pool_file(tmp_path, .75))
    monkeypatch.setattr(multibatch, 'run_case', lambda config, fleet, **kw: (config, fleet))
    cfg, fleet = main(['--case', 'config/cases/multibatch_demo.json'])
    assert cfg['container_pool_size'] == fleet['operating_policy']['container_pool_size'] == 3
    plan = run_study(['--case', 'config/cases/multibatch_demo.json', '--solver', 'alns',
                      '--seeds', '42', '--dry-run'])
    assert plan['jobs'][0]['pool'] == 3


def test_cli_orders_override_recalculates_pool(tmp_path, monkeypatch):
    from cli import multibatch
    from cli.route import main
    orders = json.loads(resolve_project_path('data/source_inputs/mechanism/orders.json').read_text())
    orders[0]['container_count'] = 5
    orders_path = write_config(tmp_path/'orders.json', orders)
    monkeypatch.setattr(multibatch, 'run_case', lambda config, fleet, **kw: (config, fleet))
    _, fleet = main(['--case', 'config/cases/multibatch_demo.json', '--orders', orders_path,
                     '--pool-config', pool_file(tmp_path, .75)])
    assert fleet['operating_policy']['container_pool_size'] == 6


def test_decimal_ratio_does_not_add_spurious_container():
    assert configured_pool({'demand_ratio': .14}, 50) == 7
    assert configured_pool({'demand_ratio': .6}, 7) == 5


def test_small_single_batch_is_rejected_without_silent_pool_increase(tmp_path):
    out = run_study(['--case', 'config/cases/single_batch_demo.json',
                     '--pool-config', pool_file(tmp_path, .5), '--solver', 'alns', '--seeds', '42',
                     '--time', '.2', '--iterations', '0', '--no-plots', '--output', str(tmp_path/'out')])
    job = load_json(out/'manifest.json')['jobs'][0]
    result = load_json(out/job['run']/'result.json')
    assert job['pool'] == 3
    assert result['info']['proven_infeasible']


def test_dataset_pairing_uses_original_inputs_when_shared_setting_changes(tmp_path, monkeypatch):
    from configuration import container_pools
    from tools.data.static_batch_dataset import check_pairing
    monkeypatch.setattr(container_pools, 'DEFAULT_POOL_CONFIG', pool_file(tmp_path, .5))
    _, records = load_manifest(MANIFEST, family_ids=['mechanism_forced_late'])
    assert check_pairing(records) == 1
