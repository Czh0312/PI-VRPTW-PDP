# Historical M8 experiments: retain their original two-stage semantics.
"""Compare exact and ALNS relay optimization using the SAME proven M3 base."""
import argparse
import json
from pathlib import Path

from configuration.loader import load_routing_case, resolve_project_path
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
from models.routing.variants.model_8.legacy.exact_solver import solve_exact
from models.routing.variants.model_8.legacy.alns import solve_alns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='config/cases/zhou_small_demo.json')
    parser.add_argument('--base-time', type=float, default=30)
    parser.add_argument('--stage2-time', type=float, default=20)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', default='output/experiments/model8_two_stage')
    args = parser.parse_args()
    cfg, fleet = load_routing_case(args.case)
    nodes, _ = parse_dataset(str(resolve_project_path(cfg['data'])))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get('speed', 1))
    problem.aggregate_demands_from_pre_assignment()
    e1, e2 = build_tasks_for_model(problem, 8)
    count = problem.fleet.e2_per_sat_count
    tfp = cfg.get('TFp', 9999.0)
    base = solve_m3(problem, e1, e2, count, TFp=tfp, verbose=False,
                    time_limit=args.base_time, return_info=True,
                    return_incumbent_on_time_limit=True,
                    gurobi_params={'MIPGap': 0, 'MIPGapAbs': 0, 'Seed': args.seed})
    result = {'case': args.case, 'seed': args.seed, 'stage2_budget_each': args.stage2_time,
              'stage1': {'e1_routes': base[0], 'e2_routes': base[1], 'info': base[2]}}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if base[2].get('proven_optimal') and base[0] is not None:
        shared = dict(problem=problem, e1_tasks=e1, e2_tasks_by_sat=e2,
                      vehicles_per_satellite=count, TFp=tfp, verbose=False,
                      base_routes=base[:2], base_info=base[2], return_info=True)
        exact = solve_exact(**shared, time_limit=args.stage2_time,
                            return_incumbent_on_time_limit=True)
        heuristic = solve_alns(**shared, max_time=args.stage2_time, seed=args.seed)
        for name, solved in [('exact', exact), ('alns', heuristic)]:
            result[name] = {'e1_routes': solved[0], 'e2_routes': solved[1], 'info': solved[2]}
        assert exact[2]['baseline']['route_fingerprint'] == heuristic[2]['baseline']['route_fingerprint']
        rows = ['| 方法 | 总距离 | 相对基础解节省 | 接力数 | 第二阶段最优证明 |',
                '| --- | ---: | ---: | ---: | --- |',
                f"| M3 基础解 | {base[2]['objective']:.6f} | 0 | 0 | 不适用 |"]
        for name in ('exact', 'alns'):
            info = result[name]['info']
            rows.append(f"| M8 {name} | {info['objective']:.6f} | "
                        f"{info['transshipment_improvement']:.6f} | "
                        f"{info['transshipment_count']} | {info['conditional_proven_optimal']} |")
        report = '\n'.join(rows)
    else:
        report = 'M3 未证明最优；按 M8 定义跳过第二阶段，不能报告最优基础解上的传递收益。'
    (output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (output / 'report.md').write_text(
        '# M8 同一 M3 最优基础解验证\n\n'
        '## Material Passport\n\n'
        '- 状态：实际运行结果，非预期结果。\n'
        f'- 输入案例：`{args.case}`；随机种子：{args.seed}。\n'
        f'- 第一阶段预算：{args.base_time}s；两种第二阶段算法各：{args.stage2_time}s。\n'
        '- 证据：同目录 `results.json`，包含基础路线、路线指纹、求解状态和交接详情。\n'
        '- 范围：仅此案例；不能推断总体优劣，坐标距离不是人民币成本。\n\n'
        + report + '\n', encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
