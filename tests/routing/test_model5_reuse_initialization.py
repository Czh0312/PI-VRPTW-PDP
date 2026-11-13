"""Input-only reuse seeds preserve physical domains and declared decision grids."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.common.cycle import load_instance
from models.routing.model_5.alns.reuse_initialization import reuse_calendar_seeds, use_reuse_seeds
from tools.configuration.loader import load_routing_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case,k', [('S03',2),('S03',3),('S09',2),('S09',3)])
@pytest.mark.parametrize('grid_seconds', [None,60])
def test_reuse_seeds_preserve_coverage_calendar_domains_and_grid(case, k, grid_seconds):
    cfg, fleet = load_routing_case(
        ROOT / f'data/benchmark27_models_1_6_v1/model_5/small/{case}/case.json',
        use_pool_config=False)
    if grid_seconds is not None:
        cfg = {**cfg, 'joint_batch_options': {**cfg['joint_batch_options'],
                                             'calendar_decision_grid_seconds':grid_seconds}}
    parent = load_instance(cfg, fleet)
    variable = parent.for_count(k)
    counts = tuple(parent.batch_count_candidates)
    rows = list(reuse_calendar_seeds(variable, limit=8))
    assert 0 < len(rows) <= 8
    assert rows == list(reuse_calendar_seeds(variable, limit=8))
    signatures = set()
    for boundaries, assignment, cutoffs in rows:
        assert parent.proposed_boundaries(k, boundaries) == boundaries
        assert set(assignment) == {o.id for o in parent.orders}
        assert set(assignment.values()) == set(variable.batch_map)
        assert all(bid in variable.legal_batches[oid] for oid,bid in assignment.items())
        child = parent.fixed_calendar(k, boundaries)
        clipped = {bid:min(hi,max(lo,cutoffs[bid])) for bid,(lo,hi) in child.cutoff_bounds.items()}
        normalized = child.proposed_cutoffs(clipped)
        assert child.validated_cutoffs(normalized) == normalized
        if grid_seconds is not None:
            step = child.calendar_decision_step
            assert all((x-boundaries[0]) % step == 0 for x in boundaries)
            assert all((x-boundaries[0]) % step == 0 for x in normalized.values())
        signature = boundaries, tuple(sorted(assignment.items())), tuple(cutoffs.values())
        assert signature not in signatures
        signatures.add(signature)
    assert tuple(parent.batch_count_candidates) == counts


def test_single_batch_and_larger_instances_keep_existing_initialization():
    assert list(reuse_calendar_seeds(SimpleNamespace(orders=[None], count=1))) == []
    assert list(reuse_calendar_seeds(SimpleNamespace(orders=[None]*10, count=2))) == []


@pytest.mark.parametrize('windows,sites,expected', [
    ([(0,2),(5,7),(10,12),(15,17)], ['s']*4, False),
    ([(0,2),(5,7),(10,12)], ['s','t','s'], False),
    ([(0,2),(5,7),(10,12),(15,17)], ['s','t','s','t'], True),
    ([(0,10),(2,12),(8,15),(14,18)], ['s','t','s','t'], False),
    ([(0,5),(4,10),(11,15),(16,20),(21,25)], ['s','t','s','t','s'], True),
])
def test_multiwave_activation_is_based_on_raw_time_and_sites(windows, sites, expected):
    orders = [SimpleNamespace(delivery_window=window, satellite=site) for window,site in zip(windows,sites)]
    assert use_reuse_seeds(SimpleNamespace(orders=orders)) is expected
