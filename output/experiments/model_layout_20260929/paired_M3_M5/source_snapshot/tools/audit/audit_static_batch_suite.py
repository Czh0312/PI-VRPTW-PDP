"""Validate all pairing contracts and independently audit constructive schedules.

A failed constructive schedule is not a proof of infeasibility. This command
does not change inputs or silently retry with longer windows/larger fleets.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from configuration.loader import load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.evaluator import evaluate
from tools.data.static_batch_dataset import check_pairing, load_manifest, verify_artifacts
from tools.data.build_static_batch_suite import read_csv


INTEGRATION_NOTE = (
    'Data audit uses satellite service_time declared in network.csv. The existing '
    'shared reader omits that field for satellites; this tool restores it only in '
    'its in-memory audit instance. Routing source code is unchanged. This report '
    'validates data feasibility, not CLI solver integration or optimality.'
)


def constructive_state(problem):
    """Fixed-input feasibility witness, independent of solver initialization."""
    f = problem.fleet
    if f.e1_count < len(problem.satellites):
        raise ValueError('This construction requires at least one E1 per satellite')
    state = dict(deliveries={}, e2=[], pickup_order={}, pickup_vehicle={})
    for b in problem.operating_policy.batches:
        state['deliveries'][b.id] = [[] for _ in range(f.e1_count)]
        state['pickup_order'][b.id] = list(problem.satellites)
        state['pickup_vehicle'][b.id] = {s: i for i, s in enumerate(problem.satellites)}
        for i, s in enumerate(problem.satellites):
            if problem.demands[s, b.id]:
                state['deliveries'][b.id][i].append(s)
    for s in problem.satellites:
        orders = sorted((o for o in problem.orders if o.satellite == s),
                        key=lambda o: (o.pickup_window[0], o.delivery_window[0], o.id))
        for index, order in enumerate(orders):
            state['e2'].append(dict(satellite=s, vehicle=index % f.e2_per_sat_count,
                                    tasks=[(order.id, 'd'), (order.id, 'p')]))
    return state


def load_declared_data(cfg, fleet):
    problem = load_instance(cfg, fleet)
    for row in read_csv(resolve_project_path(cfg['data'])):
        if row['node_type'] == 's':
            duration = float(row.get('service_time') or 0)
            if not math.isfinite(duration) or duration < 0:
                raise ValueError('Satellite service_time must be finite and nonnegative')
            problem.network.nodes[row['node_id']].service_time = duration
    return problem


def audit_dataset(manifest_path, output):
    manifest_path, output = resolve_project_path(manifest_path), resolve_project_path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Refusing to overwrite nonempty audit output')
    manifest, cases = load_manifest(manifest_path)
    verify_artifacts(manifest, manifest_path.parent)
    family_count = check_pairing(cases)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for record in cases:
        cfg, fleet = load_routing_case(record['case_path'])
        problem = load_declared_data(cfg, fleet)
        state = constructive_state(problem)
        _, result, violations = evaluate(problem, state)
        audit = audit_solution(problem, result)
        valid = not violations and audit['valid']
        result.update(solver='constructive_data_audit', audit=audit, construction_violations=violations,
                      construction='chronological_paired_trips', dataset_case=record,
                      integration_note=INTEGRATION_NOTE)
        result['info'].update(status='CONSTRUCTIVE_FEASIBLE' if valid else 'CONSTRUCTION_FAILED', proven_optimal=False)
        (output/f'{record["case_id"]}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        rows.append(dict(case_id=record['case_id'], group=record['group'], size=record['size'],
                         batches=record['batch_count'], total=problem.total, pool=problem.operating_policy.container_pool_size,
                         valid=valid, distance=audit.get('objective') if valid else None,
                         minimum_pool_for_this_schedule=audit.get('minimum_pool_for_schedule') if valid else None,
                         errors=violations+[e['code'] for e in audit['errors']]))
    report = dict(dataset_version=manifest['dataset_version'], manifest=str(manifest_path),
                  manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                  family_count=family_count, case_count=len(cases), paired_inputs_valid=True,
                  valid_constructive_schedules=sum(r['valid'] for r in rows),
                  scope='Input/pairing validation plus full-pool feasibility witnesses; no optimality claim.',
                  integration_note=INTEGRATION_NOTE, cases=rows)
    (output/'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    lines = ['静态单批／多批数据验收', '',
             f'{family_count} 组配对、{len(cases)} 个案例；输入哈希与配对条件通过。',
             f'{report["valid_constructive_schedules"]} 个确定性构造方案通过独立审计及连续两周期重放。',
             '构造使用全需求量的容器池；这些方案不声称距离最优或最小容器池。构造失败不证明模型不可行。', '',
             '本工具按 network.csv 声明的前置仓服务时间进行数据审计；当前通用读取器忽略该字段，'
             '因此仅在审计实例内补入。求解器代码未修改，本报告不代表正式求解入口已完成适配。', '',
             '| 案例 | 类别 | 规模 | 批次 | 箱次 | 构造有效 | 此时间表最小池 | 错误 |',
             '| --- | --- | --- | ---: | ---: | --- | ---: | --- |']
    for r in rows:
        lines.append(f'| [{r["case_id"]}]({r["case_id"]}.json) | {r["group"]} | {r["size"]} | {r["batches"]} | '
                     f'{r["total"]} | {r["valid"]} | {r["minimum_pool_for_this_schedule"]} | {", ".join(sorted(set(r["errors"])))} |')
    (output/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='data/multibatch/static_suite_v1/manifest.json')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    result = audit_dataset(args.manifest, args.output)
    print(f'{result["valid_constructive_schedules"]}/{result["case_count"]} constructive schedules valid')
    failed = [r['case_id'] for r in result['cases'] if not r['valid']]
    if failed:
        print('Construction failed (not infeasibility proof): '+', '.join(failed))
        raise SystemExit(1)
    return result


if __name__ == '__main__':
    main()
