"""Grouped route contacts preserve integer master optima and feasible plans."""
import pytest

from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library, build_relaxation
from experiments.runs.model8_trip_sync import augment
from tests.routing.test_model8_physical_bounds import instance


@pytest.mark.parametrize('kind', ['ordinary', 'late_release', 'multiple_satellites'])
def test_grouped_and_per_column_master_have_identical_integer_optima(kind):
    p = instance(kind)
    library, _ = enumerate_trip_library(p)
    values, counts = [], []
    for grouped in (False, True):
        m, ctx = build_relaxation(p, library, aggregate_route_links=grouped)
        try:
            augment(p, m, ctx, library, aggregate=grouped)
            m.Params.TimeLimit = 20
            m.optimize()
            assert m.Status == 2
            values.append(m.ObjVal)
            counts.append(m.NumConstrs)
        finally:
            m.dispose()
    assert values[0] == pytest.approx(values[1], abs=1e-8)
    assert counts[1] <= counts[0]
