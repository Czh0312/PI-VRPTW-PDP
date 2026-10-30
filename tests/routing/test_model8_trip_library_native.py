"""Optional native oracle must preserve every reference mask and distance."""
import time
from functools import partial
import os
from pathlib import Path
import shutil

import pytest

from experiments.runs.model8_trip_library_native import build_native, enumerate_trip_library
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case
from tests.routing.test_model8_trip_library import ROOT, compare, synthetic
from experiments.runs.research_model8_shared_relaxation import enumerate_trip_library_reference


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    compiler = shutil.which('csc')
    if compiler is None and os.name == 'nt':
        candidate = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if candidate.exists():
            compiler = str(candidate)
    if compiler is None:
        pytest.skip('Optional compiled reference oracle requires an available C# compiler')
    executable = build_native(compiler, tmp_path_factory.mktemp('m8-trip-native') / 'TripOracle.exe')
    return partial(enumerate_trip_library, native_command=executable)


@pytest.mark.parametrize('seed', range(36))
def test_native_randomized_reference_equivalence(seed, native):
    compare(synthetic(seed), enumerator=native)


@pytest.mark.parametrize('case', [f'S{i:02}' for i in range(1, 10)])
def test_native_all_nine_real_reference_libraries(case, native):
    p = load_instance(*load_routing_case(
        ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json',
        use_pool_config=False))
    compare(p, enumerator=native)


def test_native_deadline_never_returns_partial_columns(native):
    counters = {}
    with pytest.raises(TimeoutError, match='Incomplete trip enumeration'):
        native(synthetic(1), deadline=time.perf_counter()-1, stats=counters)
    assert not counters['complete']


def test_compact_columns_preserve_costs_and_binary_paths(native, tmp_path):
    p = synthetic(1)
    expected, _ = enumerate_trip_library_reference(p)
    actual, _ = native(p, include_paths=False, archive_directory=tmp_path)
    assert {r['mask']: r['distance'] for r in actual} == pytest.approx({r['mask']: r['distance'] for r in expected})
    assert all('path' not in r for r in actual)
    assert (tmp_path / 'site_00_input.bin').read_bytes()[:4] == b'M8TI'
    assert (tmp_path / 'site_00_output.bin').read_bytes()[:4] == b'M8TO'
