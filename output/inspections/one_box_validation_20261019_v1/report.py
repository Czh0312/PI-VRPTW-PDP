"""Summarize immutable first attempts; no solver and no substitution of witnesses."""
from pathlib import Path
import csv
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT/'data/formal_benchmark27_one_box_v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def number(value, digits=4):
    return '—' if value is None else f'{value:.{digits}f}' if isinstance(value, float) else str(value)


def main():
    validation = read(HERE/'validation.json')
    rows, routes = [], []
    for phase in ('s01', 'representatives'):
        folder = HERE/phase
        if not (folder/'manifest.json').exists():
            continue
        manifest = read(folder/'manifest.json')
        for job in manifest['jobs']:
            path = folder/'runs'/job['id']/'attempt_001'
            if not (path/'completion.json').exists():
                continue
            for name, expected in read(path/'completion.json')['hashes'].items():
                if hashlib.sha256((path/name).read_bytes()).hexdigest() != expected:
                    raise ValueError(f'Changed attempt artifact: {path/name}')
            row = read(path/'row.json')
            if row['valid']:
                audit = read(path/'audit.json')['audit']
                assert audit['valid'] and audit['periodic_replay']['valid']
                if row['model'] >= 3:
                    balance = audit['container_balance']
                    assert balance['issued'] == balance['e2_unloaded'] == balance['returned_to_depot'] == row['Q']
            rows.append(dict(phase=phase, **row,
                result_path=(path/'result.json').relative_to(HERE).as_posix(),
                audit_path=(path/'audit.json').relative_to(HERE).as_posix()))
            if (path/'result.json').exists():
                result = read(path/'result.json')
                routes.append(dict(id=row['id'], valid=row['valid'], model=row['model'], method=row['method'],
                    case=row['case'], batches=result.get('batches'), e1_trips=result.get('e1_trips'),
                    e2_trips=result.get('e2_trips'), result_path=rows[-1]['result_path']))
        if phase == 's01':
            for name in ('input_audit.json','witness_verification.json'):
                shutil.copyfile(folder/name, HERE/name)
    with (HERE/'small_results.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        keys = ['case','model','method','seed','status','valid','Q','N','k','objective',
                'total_distance','comparable_reusable_cost','solve_wall_seconds','best_bound',
                'mip_gap','proven_optimal','e1_trips','e2_trips','result_path','audit_path']
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction='ignore')
        writer.writeheader(); writer.writerows(sorted(rows,key=lambda r:(r['case'],r['model'],r['method'])))
    write(HERE/'routes.json', routes)
    counts = {case:dict(completed=sum(r['case']==case for r in rows),
        valid=sum(r['case']==case and r['valid'] for r in rows), planned=16)
        for case in ('S01','S04','S07')}
    first_pass = counts['S01']['completed']==counts['S01']['valid']==16
    reps_pass = all(v['completed']==v['valid']==16 for v in counts.values())
    validation.update(small_cases=counts, s01_algorithm_acceptance=first_pass,
        all_small_algorithm_acceptance=reps_pass,
        supplied_solutions_audit_passed=all(r['valid'] for r in rows if r.get('objective') is not None),
        current_dataset_manifest_sha256=hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest())
    write(HERE/'validation.json', validation)
    lines = ['# 一客一箱：数据验证与小规模实跑', '',
        '新输入：`formal_benchmark27_one_box_v1`；旧输入和冻结结果保留为“多箱需求情景”。',
        '27个基础算例、216份配置。完整货重、坐标、送取窗、车队、周期和费用系数保留。S01的Q由12改为7；优化箱池仍由M6–M8选择。',
        '精确和ALNS求解代码未修改；正式批次定义和逐例K证书不变，未加入K≤3。', '',
        'Gurobi 11.0.3经项目入口预检通过（READY，实际整数最优解1）。',
        '求解相关范围：统一MILP、原生基线、ALNS布局、算法隔离、单批独立审计：129通过、0失败、0跳过。',
        '数据选定范围：一客一箱、静态生成、旧正式配置完整性，共16项完成验证；15项在整组通过，1项修复后单独复测通过，0未解决失败、0跳过。测试XML及早期失败记录见validation.json。',
        '全部216份输入及216份独立双周期可行性见证已验证；见证不作求解初始解。当前版本完整哈希见对应input_audit.json。', '',
        '## 小规模实跑', '',
        '精确300秒、ALNS30秒、种子42，串行、精确单线程。这里只运行一个ALNS种子，不构成五种子正式结论。',
        'M1–M5原生目标为距离；M3–M8另按同一费用重计，M1/M2缺少一次性包装单价，不填可比总费用。', '',
        '| 算例 | 模型 | 算法 | 状态 | 审计 | N | K | 原生目标 | 共同费用 | 秒 | gap |',
        '| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in sorted(rows,key=lambda r:(r['case'],r['model'],r['method'])):
        gap = '—' if row.get('mip_gap') is None else f'{100*row["mip_gap"]:.2f}%'
        lines.append(f'| {row["case"]} | M{row["model"]} | {row["method"]} | {row["status"]} | '
            f'{"通过" if row["valid"] else "无有效解"} | {number(row.get("N"))} | {number(row.get("k"))} | '
            f'{number(row.get("objective"))} | {number(row.get("comparable_reusable_cost"))} | '
            f'{number(row.get("solve_wall_seconds"),2)} | {gap} |')
    lines += ['', '完整路线/批次见[routes.json](routes.json)，逐次原始结果和独立审计链接见[small_results.csv](small_results.csv)。',
        '限时未找到解不等于不可行；只有已证最优结果可用于最优误差。单种子结果不能支持总体速度或优势结论。', '',
        '## 阶段状态', '', f'S01算法验收：{"通过" if first_pass else "待完成或仍有调用未返回有效方案"}。',
        f'三规模代表例算法验收：{"通过" if reps_pass else "待完成或仍有调用未返回有效方案"}。',
        '完整正式实验为216次精确、1,080次ALNS，沿用300/30秒及种子42–46；当前状态见validation.json。',
        f'全量状态：`{validation["formal_rerun"]["status"]}`。后续控制状态见pipeline_state.json；通过代表例门槛后在独立目录重跑。',
        '原有论文的性能、最优箱池及批次数结论不能直接迁移到新版本。当前可确认的是数据业务口径与独立可行性；性能结论以完整重跑为准。']
    (HERE/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(counts,ensure_ascii=False))


if __name__ == '__main__':
    main()
