"""Freeze the route-cost probe, then add pure necessary time-domain reasoning."""
import ast
import shutil
from bench import HERE

destination = HERE/'sources/structured'
if destination.exists():
    raise FileExistsError(destination)
shutil.copytree(HERE/'sources/candidate', destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
segments = []
for name, path in [
    ('necessary_time_bounds', 'models/routing/model_8/exact/parameters.py'),
    ('productive_matching_certificate', 'models/routing/model_8/exact/preprocessing.py'),
    ('temporal_assignment_bounds', 'models/routing/model_8/exact/preprocessing.py'),
]:
    source = (HERE/'sources/baseline'/path).read_text(encoding='utf-8')
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == name)
    segments.append(ast.get_source_segment(source, node))
body = '"""Pure necessary time bounds for ALNS proposals; no optimization backend.\n\nThese input-only implications mirror the mathematical model. Domain narrowing\nuses certificates, never an incumbent or an objective reference.\n"""\n\n'+ '\n\n\n'.join(segments)+'\n\n\n'
body += '''class DomainView:
    """Narrow heuristic proposal domains without mutating the public instance."""
    def __init__(self, source, domains):
        self.source = source
        self.legal_batches = {oid: tuple(choices) for oid, choices in domains.items()}

    def __getattr__(self, name):
        return getattr(self.source, name)


def proposal_view(problem):
    bounds = temporal_assignment_bounds(problem)
    return DomainView(problem, bounds['domains']), bounds
'''
assert 'import gurobipy' not in body and 'from models.routing' not in body
(destination/'models/routing/model_8/alns/temporal_domains.py').write_text(body, encoding='utf-8')
print(str(destination))
