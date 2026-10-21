"""Pair saved audited ALNS solutions with cold, full-domain direct MILPs.

Only a proven optimum is used for optimal_gap_percent. Unproved incumbents
and lower-bound certificates are reported separately, never as optimal gaps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def worker(args):
    sys.path.insert(0, str(args.output / 'source'))
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    from tools.configuration.loader import load_routing_case

    job = json.loads((args.output / 'jobs.json').read_text())[args.worker]
    case = Path(job['input'])
    if hashlib.sha256(case.read_bytes()).hexdigest() != job['input_sha256']:
        raise ValueError('ALNS and exact inputs differ')
    problem = load_instance(*load_routing_case(case))
    alns = json.loads(Path(job['alns_result']).read_text())
    alns_audit = audit_solution(problem, alns)
    if not alns_audit['valid']:
        raise ValueError('Saved ALNS result fails independent replay')
    begun = time.perf_counter()
    exact = solve_routing(model=job['model'], method='exact', problem=problem,
                          exact_time=args.exact_seconds, seed=42,
                          exact_strategy='milp', milp_options=dict(threads=1, formulation='expanded'))
    wall = time.perf_counter() - begun
    exact_audit = audit_solution(problem, exact)
    info = exact['info']
    valid = bool(exact_audit['valid'])
    optimum = bool(valid and info.get('proven_optimal'))
    a = alns['info']['objective']
    e = info.get('objective') if valid else None
    lower = info.get('best_bound')
    if lower is not None and not math.isfinite(lower):
        lower = None
    tolerance = max(1e-5, abs(a) * 1e-7)
    conflict = ((optimum and a < e - tolerance)
                or (lower is not None and lower > a + tolerance))
    gap = max(0., a - e) / max(1e-9, abs(e)) * 100 if optimum and not conflict else None
    # For positive L <= optimum <= A: (A-optimum)/optimum <= (A-L)/L.
    upper = max(0., a - lower) / lower * 100 if lower is not None and lower > 0 and not conflict else None
    row = dict(model=job['model'], case=job['case'], alns_valid=True,
               alns_objective=a, alns_wall_seconds=job['wall_seconds'], alns_budget_seconds=job['seconds'],
               exact_status=info.get('status'), exact_valid=valid, exact_proven_optimal=optimum,
               exact_objective=e, exact_lower_bound=lower, exact_mip_gap=info.get('mip_gap'),
               exact_budget_seconds=args.exact_seconds, exact_wall_seconds=wall,
               optimal_gap_percent=gap,
               feasible_reference_difference_percent=(a - e) / max(1e-9, abs(e)) * 100 if e is not None else None,
               certified_optimal_gap_upper_percent=upper, reference_conflict=bool(conflict),
               complete_count_domain=info.get('complete_count_domain'),
               build_seconds=info.get('build_time'), mip_seconds=info.get('mip_runtime'),
               variables=info.get('variables'), constraints=info.get('constraints'),
               input=str(case), input_sha256=job['input_sha256'])
    destination = args.output / 'exact' / f'm{job["model"]}_{job["case"]}'
    write(destination / 'result.json', exact)
    write(destination / 'audit.json', exact_audit)
    write(destination / 'alns_audit.json', alns_audit)
    write(destination / 'summary.json', row)
    print(json.dumps(row), flush=True)


def summarize(output, rows):
    import csv
    write(output / 'results.json', rows)
    if rows:
        with (output / 'gaps.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ['# ALNS 对小规模精确参照的 GAP', '',
        'spacetime_v2，S01/S04/S07分别为7/10/12位顾客，种子42、哈希种子0、串行、Gurobi单线程。'
        f'ALNS复用已保存的2秒运行；精确每例预算{rows[0]["exact_budget_seconds"] if rows else "—"}秒，'
        '从零开始，完整输入域、expanded表达，无外部初解或自定义强化。', '',
        '真正的最优误差为100×(ALNS目标−已证最优目标)/|已证最优目标|。'
        '精确未证明最优时不填最优GAP；与精确最好可行解的差异另列。'
        '正下界L可提供真实最优GAP的上界100×(ALNS−L)/L，通常较松，不能当成实际误差。', '',
        '| 模型 | 已完成 | 已证最优参照 | 平均ALNS最优GAP | 最大ALNS最优GAP |', '|---|---:|---:|---:|---:|']
    for number in sorted({r['model'] for r in rows}):
        selected = [r for r in rows if r['model'] == number]
        gaps = [r['optimal_gap_percent'] for r in selected if r['optimal_gap_percent'] is not None]
        average = f'{sum(gaps)/len(gaps):.3f}%' if gaps else '—'
        maximum = f'{max(gaps):.3f}%' if gaps else '—'
        lines.append(f'| M{number} | {len(selected)} | {len(gaps)} | {average} | {maximum} |')
    lines += ['', '以上平均只覆盖具有已证最优参照的案例；不能将未证明或未找到解的案例按0%计入平均。', '',
        '## 真实最优GAP的可证区间', '',
        '0%表示已证最优参照下达到最优值。区间不是实际误差点值，而是由已审计可行解及完整模型下界限制的范围；'
        '显示时下端向下、上端向上取整至两位小数。没有正下界或上界超过100%时标为“界过松”。', '',
        '| 模型 | S01：7顾客 | S04：10顾客 | S07：12顾客 |', '|---|---:|---:|---:|']
    for model in sorted({r['model'] for r in rows}):
        cells = []
        for case in ('S01', 'S04', 'S07'):
            item = next((r for r in rows if r['model'] == model and r['case'] == case), None)
            if item is None:
                cells.append('待完成')
                continue
            if item['optimal_gap_percent'] is not None:
                cells.append(f'{item["optimal_gap_percent"]:.2f}%')
                continue
            a, e = item['alns_objective'], item['exact_objective']
            best = min(a, e) if e is not None else a
            lower = max(0., a - best) / max(1e-9, abs(best)) * 100
            upper = item['certified_optimal_gap_upper_percent']
            if upper is None or upper > 100:
                cells.append(f'≥{math.floor(lower * 100) / 100:.2f}%（界过松）' if lower > 1e-6 else '未确定（界过松）')
            else:
                cells.append(f'[{math.floor(lower * 100) / 100:.2f}%, {math.ceil(upper * 100) / 100:.2f}%]')
        lines.append(f'| M{model} | ' + ' | '.join(cells) + ' |')
    lines += ['', '## 全部原始配对', '',
        '| 模型/算例 | ALNS目标 | 精确目标 | 精确状态 | ALNS最优GAP | 相对精确可行解差异 | 精确下界 |',
        '|---|---:|---:|---|---:|---:|---:|']
    def number(value, suffix=''):
        return f'{value:.4f}{suffix}' if value is not None else '—'
    for r in rows:
        lines.append(f'| M{r["model"]}/{r["case"]} | {number(r["alns_objective"])} | '
            f'{number(r["exact_objective"])} | {r["exact_status"]} | '
            f'{number(r["optimal_gap_percent"], "%")} | '
            f'{number(r["feasible_reference_difference_percent"], "%")} | '
            f'{number(r["exact_lower_bound"])} |')
    lines += ['', '相对可行解差异为负只说明ALNS优于本次Gurobi已找到的方案，不代表负的最优GAP。'
        '原始方案、独立审计和日志保留在exact目录。数值容差内的最优误差按0处理；任何参照冲突独立标记。', '']
    (output / 'report.md').write_text('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alns-study', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--exact-seconds', type=float, default=30)
    parser.add_argument('--worker', type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output, args.alns_study = args.output.resolve(), args.alns_study.resolve()
    if args.exact_seconds <= 0:
        parser.error('Require a positive exact budget')
    if args.worker is not None:
        worker(args)
        return
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output must be new or empty')
    manifest = json.loads((args.alns_study / 'manifest.json').read_text())
    sources = manifest['source_sha256']['candidate']
    if any(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h for p, h in sources.items()):
        raise ValueError('Model source changed since the saved ALNS runs')
    for name, digest in json.loads((args.alns_study / 'input_sha256.json').read_text()).items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Input changed since the saved ALNS runs')
    args.output.mkdir(parents=True, exist_ok=True)
    jobs = []
    for row in json.loads((args.alns_study / 'results.json').read_text()):
        result_path = args.alns_study / row['variant'] / f'm{row["model"]}_{row["case"]}' / 'result.json'
        jobs.append({**row, 'alns_result': str(result_path)})
    write(args.output / 'jobs.json', jobs)
    source = args.output / 'source'
    with zipfile.ZipFile(args.output / 'source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for folder in ('models', 'tools'):
            for path in sorted((ROOT / folder).rglob('*.py')):
                target = source / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                archive.write(path, path.relative_to(ROOT))
    for folder in ('data', 'config', 'archive'):
        (source / folder).symlink_to(ROOT / folder, target_is_directory=True)
    shutil.copyfile(args.alns_study / 'inputs.zip', args.output / 'inputs.zip')
    shutil.copyfile(args.alns_study / 'input_sha256.json', args.output / 'input_sha256.json')
    write(args.output / 'manifest.json', dict(alns_study=str(args.alns_study), exact_seconds=args.exact_seconds,
        seed=42, python_hash_seed=0, concurrency=1, source_sha256=sources, python=sys.version,
        milp_options=dict(threads=1, formulation='expanded'), external_start=False))
    environment = {**os.environ, 'PYTHONHASHSEED': '0', **{key: '1' for key in
        ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    rows = []
    for index, job in enumerate(jobs):
        command = [sys.executable, str(Path(__file__).resolve()), '--alns-study', str(args.alns_study),
                   '--output', str(args.output), '--exact-seconds', str(args.exact_seconds), '--worker', str(index)]
        completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                   text=True, timeout=args.exact_seconds + 90)
        destination = args.output / 'exact' / f'm{job["model"]}_{job["case"]}'
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'worker.log').write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'Exact reference failed: {destination}/worker.log')
        row = json.loads((destination / 'summary.json').read_text())
        rows.append(row)
        summarize(args.output, rows)
        print(job['model'], job['case'], row['exact_status'], 'ALNS_GAP', row['optimal_gap_percent'],
              'feasible_difference', row['feasible_reference_difference_percent'], flush=True)
        if row['reference_conflict']:
            raise RuntimeError('An exact certificate conflicts with the audited ALNS solution')


if __name__ == '__main__':
    main()
