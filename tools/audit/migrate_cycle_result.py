"""Copy and re-audit a former M3 multi-batch JSON under the corrected M5 label."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.configuration.loader import load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.migration import migrate_former_m3_result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--route-result', required=True)
    parser.add_argument('--output', required=True, help='New JSON file; source is never overwritten')
    args = parser.parse_args(argv)
    cfg, fleet = load_routing_case(args.case)
    problem = load_instance({**cfg, 'model': 5}, fleet)
    source, target = resolve_project_path(args.route_result), resolve_project_path(args.output)
    payload = source.read_bytes()
    result = migrate_former_m3_result(problem, json.loads(payload))
    result['label_migration'].update(source=str(source), source_sha256=hashlib.sha256(payload).hexdigest())
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(f'Current M5; audited={result["audit"]["valid"]}: {target}')
    return result


if __name__ == '__main__':
    main()
