"""Render the diagnostic results and verify that model sources stayed fixed."""
import hashlib
import json
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def log_metrics(path):
    text = path.read_text(encoding='utf-8', errors='replace')
    result = dict(warnings=[line.strip() for line in text.splitlines() if 'warning' in line.lower()])
    for key, pattern in (
        ('root_objective', r'Root relaxation: objective ([0-9.e+\-]+)'),
        ('root_lp_seconds', r'Root relaxation:.*?([0-9.]+) seconds'),
        ('presolve_seconds', r'Presolve time: ([0-9.]+)s'),
    ):
        found = re.search(pattern, text)
        result[key] = float(found.group(1)) if found else None
    candidates = [line for line in text.splitlines() if re.match(r'^[H*]', line)]
    result['first_incumbent_log_line'] = candidates[0] if candidates else None
    heuristic = re.search(r'Found heuristic solution: objective ([0-9.e+\-]+)', text)
    result['presolve_heuristic_objective'] = float(heuristic.group(1)) if heuristic else None
    result['first_branch_log_seconds'] = None
    for line in text.splitlines():
        branch = re.match(r'^\s*(\d+)\s+(\d+)\s+.*?\s+(\d+)s\s*$', line)
        if branch and (int(branch.group(1)) > 0 or int(branch.group(2)) > 0):
            result['first_branch_log_seconds'] = int(branch.group(3))
            break
    return result


def main():
    manifest = read(OUT/'manifest.json')
    rows = read(OUT/'summary.json')
    assert manifest['completed'] == len(manifest['jobs']) == len(rows) == 20
    assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == expected
               for name, expected in manifest['source_hashes'].items())
    baseline_inputs = {}
    for model in (6, 7, 8):
        evidence = read(ROOT/f'output/inspections/small_exact_m5_m8_20261019_v1/runs/S04_m{model}/manifest.json')
        for name, expected in evidence['input_sha256'].items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected
            baseline_inputs[name] = expected
    consistency = [row for row in rows if row['kind'] == 'consistency']
    performance = [row for row in rows if row['kind'] == 'performance']
    assert len(consistency) == 12 and all(row['valid'] for row in consistency)
    metrics = {row['id']: log_metrics(OUT/'runs'/row['id']/'gurobi.log') for row in rows}
    hashes = {}
    for row in rows:
        path = OUT/'runs'/row['id']
        saved_row = read(path/'row.json')
        assert saved_row == row
        if row['valid']:
            result = read(path/'result.json')
            assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            assert result['info']['objective'] == row['objective']
        for file in path.iterdir():
            if file.is_file():
                hashes[file.relative_to(OUT).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    checks = dict(valid=True, consistency_passed=12, consistency_failed=0,
                  performance_runs=len(performance), audited_performance_solutions=sum(row['valid'] for row in performance),
                  unchanged_model_source_files=len(manifest['source_hashes']), artifact_sha256=hashes,
                  baseline_input_sha256_matched=baseline_inputs,
                  diagnostic_driver_sha256=hashlib.sha256((OUT/'diagnose.py').read_bytes()).hexdigest())
    (OUT/'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT/'log_metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# S04（10 顾客）：MILP 一致性与表达对照诊断', '',
             f"源码提交 `{manifest['commit']}`；模型源码未修改。单进程串行，Gurobi 单线程，seed=42。",
             '性能运行每次 300 秒，包含建模；保留 Gurobi 默认预处理、割和启发式，没有外部初解、回调或求解参数调优。', '',
             '## 一致性检查', '',
             '12/12 通过：M6/M7/M8 × expanded/compact × 固定 K=1/完整 K 域统一模型。',
             '将已独立审计的 K=1 调度映射到 MILP，固定路线、配送分批、箱池、时刻、数量和日历；辅助变量仍由 Gurobi 完成。',
             '全部得到相同调度成本 104.4050041044，提取结果通过业务审计及两周期重放。',
             '这证明该具体合法调度可在上述表达中表示，不代表所有约束均已被穷尽验证，也不证明原问题全局最优。', '',
             '## 自由求解性能', '',
             '| 模型 | 域 | 表达 | 状态 | 审计有效 | 目标 | 下界 | gap | 变量 | 二元变量 | 约束含指示 | 建模秒 | MIP秒 | 节点 |',
             '| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    def fmt(value):
        return '—' if value is None else f'{value:.4f}'
    for row in performance:
        domain = '完整 K 域' if row['scope'] == 'unified' else f"固定 K={row['count']}"
        gap = '—' if row['gap'] is None else f"{100*row['gap']:.2f}%"
        lines.append(f"| M{row['model']} | {domain} | [{row['formulation']}](runs/{row['id']}/gurobi.log) | "
                     f"{row['status']} | {row['valid']} | {fmt(row['objective'])} | {fmt(row['bound'])} | {gap} | "
                     f"{row['variables']} | {row['binaries']} | {row['linear_constraints']+row['indicators']} | "
                     f"{row['build_seconds']:.2f} | {row['mip_seconds']:.2f} | {row['nodes']:.0f} |")
    lines += ['', '固定 K 的最优性只针对该 K，不是完整输入域的全局最优。不同 K 对应不同受限问题，不能据求解速度直接判定表达优劣。',
              'M7/M8 本轮只补测 compact；expanded 参照上轮同源码、同配置的 300 秒结果，不是本轮同时段成对复测。',
              '两种表达可行域等价不保证相同预算下必然得到相同质量的限时解。', '', '## 日志中的根节点信息', '',
              '| 运行 | Presolve 秒 | 初始根松弛目标 | 根 LP 秒 | 首次分支日志秒 |', '| --- | ---: | ---: | ---: | ---: |']
    for row in performance:
        metric = metrics[row['id']]
        lines.append(f"| {row['id']} | {fmt(metric['presolve_seconds'])} | {fmt(metric['root_objective'])} | "
                     f"{fmt(metric['root_lp_seconds'])} | {fmt(metric['first_branch_log_seconds'])} |")
    lines += ['', '根 LP 秒不包含后续根节点切割、启发式和分支搜索。日志时间仅作阶段定位，不当作首次可行解的精确时刻。',
              '本轮只诊断 S04，不能外推为全部正式数据的性能结论。业务允许域、费用和时间精度均未修改。',
              '初次运行在进入优化前遇到 Gurobi Unicode 绝对日志路径兼容问题，已改用 ASCII 相对路径；失败记录保存在 failed_attempts。', '']
    prototype_path = OUT/'prototype_summary.json'
    if prototype_path.exists():
        prototype = read(prototype_path)
        assert len(prototype) == 4 and all(row['valid'] for row in prototype if row['kind'] == 'consistency')
        lines += ['## 隔离原型：分块指示约束的线性改写', '',
                  '只在独立诊断子进程中替换分块装配；生产模块文件没有修改。',
                  '将每条源指示约束 L(x)≤0 的界写成 L(x^k)≤U·(δ_k−h)（h=1 触发），'
                  '或 L(x^k)≤U·h（h=0 触发）；U 从源变量有限界计算。大于等于及等式同理。',
                  'δ=1 时恢复原有有界指示约束，δ=0 时整块为零，因此整数可行域和目标保持不变。'
                  '没有把 K 选择移到求解器外，也未缩小 K 域。', '',
                  '| 模型/阶段 | 状态 | 审计有效 | 目标 | 下界 | gap | 变量 | 约束 | 建模秒 | MIP秒 | 节点 |',
                  '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        prototype_phase_notes = []
        for row in prototype:
            metric = log_metrics(OUT/'runs'/row['id']/'gurobi.log')
            metrics[row['id']] = metric
            if row['valid']:
                result = read(OUT/'runs'/row['id']/'result.json')
                assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
            assert read(OUT/'runs'/row['id']/'row.json') == row
            for file in (OUT/'runs'/row['id']).iterdir():
                if file.is_file():
                    hashes[file.relative_to(OUT).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
            gap = '—' if row['gap'] is None else f"{100*row['gap']:.2f}%"
            lines.append(f"| [M{row['model']}/{row['kind']}](runs/{row['id']}/gurobi.log) | {row['status']} | {row['valid']} | {fmt(row['objective'])} | "
                         f"{fmt(row['bound'])} | {gap} | {row['variables']} | "
                         f"{row['linear_constraints']+row['indicators']} | {row['build_seconds']:.2f} | "
                         f"{row['mip_seconds']:.2f} | {row['nodes']:.0f} |")
            if row['kind'] == 'performance':
                prototype_phase_notes.append(f"- M{row['model']} 原型：根 LP {fmt(metric['root_lp_seconds'])} 秒；"
                    f"首次分支日志时间 {fmt(metric['first_branch_log_seconds'])} 秒；"
                    f"预处理后启发式候选目标 {fmt(metric['presolve_heuristic_objective'])}。")
        lines += ['']+prototype_phase_notes
        lines += ['', '原型结果仅针对 S04 的 M6/M8/compact；不能据一个算例决定替换全部模型的默认表达。', '']
        checks.update(prototype_consistency_passed=sum(row['valid'] for row in prototype if row['kind'] == 'consistency'),
                      prototype_performance_valid=sum(row['valid'] for row in prototype if row['kind'] == 'performance'),
                      prototype_performance_status={row['model']: row['status'] for row in prototype if row['kind'] == 'performance'})
        (OUT/'log_metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    validation_path = OUT/'validation.json'
    if validation_path.exists():
        validation = read(validation_path)
        assert validation['exit_code'] == 0 and validation['failed'] == validation['skipped'] == 0
        checks['selected_pytest_validation'] = validation
        lines += ['## 定向测试验证', '',
                  f"{validation['passed']} 项通过、0 失败、0 跳过。选定范围：", '']
        lines += [f'- `{name}`' for name in validation['selection']]
        lines += ['', '这些是定向测试，不是完整测试套件。限时可行解也不代表各模型的真实最优成本，不能据此作模型经济性排序。', '']
    (OUT/'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({key: value for key, value in checks.items()
                      if key not in ('artifact_sha256', 'baseline_input_sha256_matched')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
