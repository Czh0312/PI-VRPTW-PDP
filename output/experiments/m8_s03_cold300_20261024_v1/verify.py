"""Independent audit of the frozen S03 run; never supplies an initial solution."""
import hashlib
import json
import math
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE/'source'))

from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main():
    frozen = read(HERE/'freeze_manifest.json')
    for relative, digest in frozen['source_hashes'].items():
        assert hashlib.sha256((HERE/'source'/relative).read_bytes()).hexdigest() == digest, relative
    for key, item in frozen['input_snapshots'].items():
        assert hashlib.sha256(Path(item['snapshot']).read_bytes()).hexdigest() == item['sha256'], key
    run = HERE/'run_frozen'
    manifest = read(run/'manifest.json')
    for relative, digest in manifest['source_hashes'].items():
        assert hashlib.sha256((HERE/'source'/relative).read_bytes()).hexdigest() == digest, relative
    for absolute, digest in manifest['input_hashes'].items():
        assert hashlib.sha256(Path(absolute).read_bytes()).hexdigest() == digest, absolute
    problem = load_instance(*load_routing_case(HERE/'inputs/case.json', use_pool_config=False))
    summary = read(run/'physical_time_summary.json')
    assert len(problem.orders) == len({o.customer for o in problem.orders}) == 7
    assert list(problem.batch_count_candidates) == summary['batch_count_candidates'] == list(range(1, 8))
    assert summary['complete_count_domain']
    assert summary['mip_start_supplied'] is False and manifest['external_starts'] is False
    assert manifest['solution_limit'] is None and manifest['seconds'] == 300
    checks = dict(case='S03', customers=7, complete_K_domain=list(range(1, 8)),
                  status=summary['status'], native_Gurobi_optimal=summary['proven_optimal'],
                  objective=summary['objective'], best_bound=summary['best_bound'], mip_gap=summary['mip_gap'],
                  first_solver_incumbent_seconds=summary['first_solver_incumbent_seconds'],
                  build_seconds=summary['build_time'], optimization_seconds=summary['mip_runtime'],
                  solve_wall_seconds=summary['wall_seconds'],
                  source_files_verified=len(frozen['source_hashes']),
                  source_identical_to_S02=frozen['source_identical_to_S02'],
                  source_and_inputs_unchanged=True, external_start=False, custom_cuts=False,
                  selected_scope='One S03 exact solve; standalone business, cost and two-cycle replay; no unit tests rerun')
    if summary['sol_count']:
        result = read(run/'physical_time_result.json')
        begun = time.perf_counter()
        audit = audit_solution(problem, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        cost = evaluate_comparison_cost(problem, result, problem.cost_profile.config, audit=audit)
        assert math.isclose(audit['objective'], summary['objective'], abs_tol=1e-7)
        assert math.isclose(cost['total'], summary['objective'], abs_tol=1e-7)
        checks.update(selected_K=result['cycle_batch_count'], pool=result['container_pool_size'],
                      business_audit_valid=True, periodic_replay_valid=True,
                      independent_cost=cost['total'], independent_audit_seconds=time.perf_counter()-begun)
        write(HERE/'independent_audit.json', audit)
        write(HERE/'independent_cost.json', cost)
    if summary['proven_optimal']:
        assert summary['status'] == 'OPTIMAL' and summary['mip_gap'] == 0
        assert math.isclose(summary['objective'], summary['best_bound'], abs_tol=1e-7)
        assert checks['business_audit_valid']
    old_path = PROJECT/'output/experiments/small7_m8_alns300_20261024_v1/runs/S03_m8_alns_300s_s42/attempt_001/result.json'
    old = read(old_path)
    old_audit = audit_solution(problem, old)
    assert old_audit['valid'] and old_audit['periodic_replay']['valid']
    checks['old_ALNS_cost'] = old_audit['objective']
    if summary['objective'] is not None:
        checks['improvement_over_saved_ALNS_percent'] = 100*(old_audit['objective']-summary['objective'])/old_audit['objective']
    write(HERE/'verification.json', checks)
    def number(value):
        return '—' if value is None else f'{value:.6f}'
    lines = ['# 模型八：7顾客算例S03精确求解', '',
             '冻结完整输入与源码；源码303个文件与S02快照一致。保留原K=1～7、秒级物理输入及逐K细分时钟、车队、箱池域和费用。',
             'physical_time增强、compact表达、单线程、种子42，冷启动，无ALNS初解、变量提示或自定义割；只读回调记录事件。',
             '优化上限300秒，建模和审计另计；未设置首解停止。', '',
             '| 指标 | 结果 |', '|---|---|',
             f"| Gurobi原生状态 | {summary['status']} |",
             f"| 成本 | {number(summary['objective'])} |",
             f"| 完整K域下界 | {number(summary['best_bound'])} |",
             f"| GAP（比例） | {number(summary['mip_gap'])} |",
             f"| K / 箱池 | {checks.get('selected_K', '—')} / {checks.get('pool', '—')} |",
             f"| 首个求解器可行解（优化秒） | {number(summary['first_solver_incumbent_seconds'])} |",
             f"| 建模秒 | {number(summary['build_time'])} |",
             f"| 优化秒 | {number(summary['mip_runtime'])} |",
             f"| 含建模、提取及内部审计的调用秒 | {number(summary['wall_seconds'])} |",
             '| 输入、源码哈希复核 | 通过 |']
    if summary['sol_count']:
        lines += ['| 独立业务/成本审计 | 通过 |', '| 两周期重放 | 通过 |', '',
                  f"已有300秒ALNS方案在本次冻结输入下重新审计成本为{number(checks['old_ALNS_cost'])}；本次精确结果成本降低{checks['improvement_over_saved_ALNS_percent']:.4f}%。旧方案只在求解后比较，未作为初解。"]
    else:
        lines += ['', '本次预算内没有整数可行解，不能把TIME_LIMIT解释成不可行证明。已有ALNS方案独立审计通过。']
    lines += ['', '原生最优状态仅在OPTIMAL且最终方案独立审计通过时成立；下界覆盖原输入全部K，未固定K或人工日历。',
              '首解时刻是Gurobi接收整数解事件的时间；独立进程核验的是最终方案。',
              '本轮仅新增一次S03求解及独立审计，未修改生产模型，也未重新运行单元测试套件。',
              '原生日志、解及摘要见run_frozen/；独立复核见verification.json、independent_audit.json与independent_cost.json。', '']
    (HERE/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
