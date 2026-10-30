from pathlib import Path
from experiments.runs.research_model8_shared_relaxation import build_relaxation as _build
from experiments.runs.model8_trip_library_native import enumerate_trip_library as _enumerate
HERE = Path(__file__).resolve().parent
def build_relaxation(problem, library):
    return _build(problem, library, max_orders=20, aggregate_route_links=True)
def enumerate_trip_library(problem, **kwargs):
    return _enumerate(problem, native_command=HERE/'TripOracle.exe', include_paths=False,
                      archive_directory=HERE/'native_routes', **kwargs)
