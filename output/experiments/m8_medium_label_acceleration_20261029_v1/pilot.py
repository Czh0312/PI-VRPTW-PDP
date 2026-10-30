"""Bounded enumeration pilot before freezing the full timing run."""
from pathlib import Path
import gzip
import hashlib
import json
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from experiments.runs.model8_trip_library import enumerate_trip_library
from models.common.cycle import load_instance
from tools.configuration.loader import load_routing_case


def save(name, value):
    path = HERE / name
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


if __name__ == '__main__':
    p = load_instance(*load_routing_case(
        ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1/model_8/medium/M01/case.json',
        use_pool_config=False))
    source = ROOT / 'experiments/runs/model8_trip_library.py'
    initial_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    begun = time.perf_counter()
    counters = {}
    try:
        library, prefixes = enumerate_trip_library(p, deadline=begun+180, stats=counters,
                                                   on_progress=lambda x: save('pilot_progress.json', x))
        enumeration_seconds = time.perf_counter()-begun
        with gzip.open(HERE / 'pilot_library.json.gz', 'wt', encoding='utf-8', compresslevel=1) as stream:
            json.dump(library, stream, separators=(',', ':'))
        counters['enumeration_seconds'] = enumeration_seconds
        counters['export_seconds'] = time.perf_counter()-begun-enumeration_seconds
    except TimeoutError as exc:
        counters['stop_reason'] = str(exc)
    counters['source_unchanged'] = initial_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    save('pilot_summary.json', counters)
    print(json.dumps(counters), flush=True)
