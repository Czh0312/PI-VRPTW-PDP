"""Public numbered folders must enforce and dispatch the user's model design."""
from copy import deepcopy
from importlib import import_module

import pytest

from models.routing.catalog import MODEL_SPECS
from models.routing.service import solve_routing
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.migration import migrate_former_m3_result
from models.routing.common.cycle import MultiBatchInstance, load_instance
from tools.configuration.loader import load_routing_case
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_single_batch_comparison import problem


@pytest.mark.parametrize('number', [1, 2, 3, 4, 5])
def test_dispatch_uses_each_numbered_public_entry(monkeypatch, number):
    instance = fixture() if number == 5 else problem(number)
    mode = 'multi_batch' if number == 5 else 'single_batch'
    path = MODEL_SPECS[number].module_path(mode)
    assert path == f'models.routing.variants.model_{number}'
    module = import_module(path)
    assert module.MODEL_NUMBER == number and module.BATCH_MODE == mode
    assert import_module(path+'.model').validate_problem(instance) is None
    for method in ('exact', 'alns'):
        package = import_module(path+'.'+method)
        assert hasattr(package, '__path__')
        assert callable(getattr(package, 'solve_'+method))
        assert import_module(path+'.'+method+'.solver')
    called = []
    def spy(p, **kwargs):
        called.append(p)
        return {'entry': path}
    for method in ('alns', 'exact'):
        monkeypatch.setattr(module, 'solve_'+method, spy)
        assert solve_routing(model=number, method=method, problem=instance) == {'entry': path}
    assert called == [instance, instance]


@pytest.mark.parametrize('number', [1, 2, 3, 4, 5])
def test_real_entry_emits_its_model_identity(number):
    instance = fixture() if number == 5 else problem(number)
    module = import_module(f'models.routing.variants.model_{number}')
    result = module.solve_alns(instance, max_time=.1, max_iterations=30)
    assert result['model'] == number
    assert result['model_module'] == module.__name__
    assert result['batch_mode'] == module.BATCH_MODE
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']


def test_single_and_multi_entries_cannot_be_interchanged():
    single, multi = fixture(one_batch=True), fixture()
    m3, m5 = (import_module(f'models.routing.variants.model_{n}') for n in (3, 5))
    with pytest.raises(ValueError, match='M3 entry'):
        m3.solve_alns(multi)
    with pytest.raises(ValueError, match='M5 entry'):
        m5.solve_alns(single)
    with pytest.raises(ValueError, match='Model 5'):
        MultiBatchInstance(multi.network, multi.orders, multi.operating_policy, model=3)
    with pytest.raises(ValueError, match='Model 5'):
        MultiBatchInstance(single.network, single.orders, single.operating_policy, model=5)
    with pytest.raises(ValueError, match='historical M5 was retired'):
        from types import SimpleNamespace
        solve_routing(model=5, method='alns', problem=SimpleNamespace(), allow_baseline=True)


def test_multibatch_default_case_selects_new_model5():
    cfg, fleet = load_routing_case('config/experiments/examples/multibatch_demo.json')
    assert cfg['model'] == 5
    instance = load_instance(cfg, fleet)
    assert instance.model_number == 5 and instance.reusable and not instance.free_assignment


def test_former_m3_output_requires_explicit_audited_migration():
    instance = fixture()
    old = hand_solution(instance)
    old['model'] = 3
    original = deepcopy(old)
    assert not audit_solution(instance, old)['valid']
    updated = migrate_former_m3_result(instance, old)
    assert updated['model'] == 5 and updated['audit']['valid']
    assert updated['e1_trips'] == old['e1_trips'] and updated['e2_trips'] == old['e2_trips']
    assert old == original
    for changed in ({**old, 'model': 5}, {**old, 'schema_version': 1}, {**old, 'policy_version': 'legacy'}):
        with pytest.raises(ValueError, match='former M3'):
            migrate_former_m3_result(instance, changed)
    old['e1_trips'][0]['events'][1]['quantity'] += 1
    with pytest.raises(ValueError, match='failed independent audit'):
        migrate_former_m3_result(instance, old)
