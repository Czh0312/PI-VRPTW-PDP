"""Same M01, compiled exhaustive enumeration, preserving the original domain."""
from pathlib import Path
import gzip
import json
import os
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from experiments.runs.model8_trip_library_native import build_native, enumerate_trip_library
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case


def save(name, value):
    path = HERE / name
    pending = path.with_suffix(path.suffix+'.tmp')
    pending.write_text(json.dumps(value), encoding='utf-8')
    pending.replace(path)


if __name__ == '__main__':
    begun = time.perf_counter()
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    executable = build_native(compiler, HERE / 'TripOracle.exe')
    compile_seconds = time.perf_counter()-begun
    p = load_instance(*load_routing_case(
        ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/medium/M01/case.json',
        use_pool_config=False))
    counters = {}
    try:
        library, prefixes = enumerate_trip_library(p, native_command=executable, deadline=begun+180,
            stats=counters, on_progress=lambda x: save('native_progress.json', x))
        enumeration_seconds = time.perf_counter()-begun
        with gzip.open(HERE / 'native_library.json.gz', 'wt', encoding='utf-8', compresslevel=1) as stream:
            json.dump(library, stream, separators=(',', ':'))
        counters['enumeration_with_compile_seconds'] = enumeration_seconds
        counters['export_seconds'] = time.perf_counter()-begun-enumeration_seconds
    except TimeoutError as exc:
        counters['stop_reason'] = str(exc)
    counters['compile_seconds'] = compile_seconds
    save('native_summary.json', counters)
    print(json.dumps(counters), flush=True)
