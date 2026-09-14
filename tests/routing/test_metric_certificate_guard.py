"""Metric distance alone cannot justify a shortcut through tight time windows."""
from types import SimpleNamespace
import pytest
pytest.importorskip('gurobipy')
from models.routing.variants.model_5.exact_solver import _has_metric_distances


def test_nonmetric_travel_time_disables_relay_shortcut_certificate():
    p = SimpleNamespace(depot=SimpleNamespace(id='D'),
        satellites=[SimpleNamespace(id='A'),SimpleNamespace(id='B')],
        distance=lambda i,j: 0 if i==j else 1,
        travel_time=lambda i,j: 0 if i==j else (10 if (i,j)==('D','B') else 1))
    assert not _has_metric_distances(p,[],{})
    p.travel_time = p.distance
    assert _has_metric_distances(p,[],{})


def test_free_assignment_guard_includes_customers_without_input_tasks():
    p = SimpleNamespace(depot=SimpleNamespace(id='D'),
        satellites=[SimpleNamespace(id='S')], customers=[SimpleNamespace(id='C')],
        distance=lambda i,j: 0 if i==j else (10 if (i,j)==('D','C') else 1),
        travel_time=lambda i,j: 0 if i==j else 1)
    assert not _has_metric_distances(p,[],{})
