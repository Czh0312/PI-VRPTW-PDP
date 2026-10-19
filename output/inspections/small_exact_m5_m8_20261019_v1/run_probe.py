"""Run the 36 formal small cases through the current production exact CLI."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BUDGET = 300


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def summarize(rows):
    ordered = sorted(rows, key=lambda r: (r['model'], r['case']))
    write_json(OUT / 'summary.json', ordered)
    lines = ['# M5–M8 当前直接 Gurobi：正式小规模实跑', '',
             f'完成 {len(rows)}/36；每例 {BUDGET} 秒总求解预算，单线程，seed=42，expanded 表达。',
             '使用原始正式配置的完整 K 域；无外部初解、定制搜索或人为缩小域。',
             'M5 目标为距离，M6–M8 目标为成本；不同目标数值不能直接比较。', '',
             '| 模型 | 已完成 | 审计有效可行解 | 已证明最优 | 已证明不可行 | 无有效解 |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for model in range(5, 9):
        group = [r for r in rows if r['model'] == model]
        lines.append(f"| M{model} | {len(group)}/9 | {sum(r['valid'] for r in group)} | "
                     f"{sum(r['proven_optimal'] for r in group)} | "
                     f"{sum(r['proven_infeasible'] for r in group)} | {sum(not r['valid'] for r in group)} |")
    lines += ['', '| 模型 | 算例 | 顾客数 | K域 | 状态 | 审计有效 | 目标 | 下界 | gap | 选定K | 建模秒 | MIP秒 | 墙钟秒 |',
              '| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    def num(value):
        return '—' if value is None else f'{value:.4f}'
    for row in ordered:
        gap = '—' if row.get('gap') is None else f"{100*row['gap']:.2f}%"
        lines.append(f"| M{row['model']} | [{row['case']}]({row['run_dir']}/result.json) | "
                     f"{row.get('customer_count', '—')} | {row.get('counts', '—')} | {row['status']} | "
                     f"{row['valid']} | {num(row.get('objective'))} | {num(row.get('bound'))} | {gap} | "
                     f"{row.get('selected_k') or '—'} | {num(row.get('build_time'))} | "
                     f"{num(row.get('mip_time'))} | {num(row.get('elapsed'))} |")
    lines += ['', '限时未找到有效解不等于证明不可行。证明最优仅指完整输入域和声明的整数时间精度。',
              '原始 result.json、求解日志、输入哈希与源码快照保存在各运行目录；本报告不覆盖旧冻结实验。', '']
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def run_one(model, case):
    run_dir = OUT / 'runs' / f'{case}_m{model}'
    case_path = ROOT / f'data/formal_benchmark27_v1/model_{model}/small/{case}/case.json'
    cfg = json.loads(case_path.read_text(encoding='utf-8'))
    row = dict(model=model, case=case, run_dir=run_dir.relative_to(OUT).as_posix(),
               counts=cfg.get('joint_batch_options', {}).get('batch_count_candidates', [3]),
               customer_count=7 if case <= 'S03' else 10 if case <= 'S06' else 12,
               started_at=now(), valid=False, proven_optimal=False, proven_infeasible=False)
    cmd = [sys.executable, '-X', 'utf8', str(ROOT / 'main.py'), 'route', '--case', str(case_path),
           '--solver', 'exact', '--exact-strategy', 'milp', '--exact-time', str(BUDGET),
           '--milp-threads', '1', '--seed', '42', '--output', str(run_dir)]
    log_path = OUT / 'logs' / f'{case}_m{model}.log'
    begun = time.perf_counter()
    print(json.dumps(dict(event='start', model=model, case=case, at=now())), flush=True)
    try:
        with log_path.open('w', encoding='utf-8') as log:
            result = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=BUDGET + 180, check=False)
        row['exit_code'] = result.returncode
        path = run_dir / 'result.json'
        if not path.exists():
            raise RuntimeError(f'No result.json; see {log_path.name}; exit={result.returncode}')
        raw = json.loads(path.read_text(encoding='utf-8'))
        info, audit = raw['info'], raw.get('audit', {})
        row.update(status=info['status'], objective=info.get('objective'),
                   valid=bool(audit.get('valid') and info.get('objective') is not None),
                   proven_optimal=bool(info.get('proven_optimal') and audit.get('valid')),
                   proven_infeasible=bool(info.get('proven_infeasible')),
                   bound=info.get('best_bound'), gap=info.get('mip_gap'),
                   selected_k=raw.get('cycle_batch_count', 3 if model == 5 else None),
                   pool=raw.get('container_pool_size'), build_time=info.get('build_time'),
                   mip_time=info.get('mip_runtime'), variables=info.get('variables'),
                   constraints=info.get('constraints'), sol_count=info.get('sol_count'),
                   node_count=info.get('node_count'), strategy=info.get('exact_strategy'),
                   complete_count_domain=info.get('complete_count_domain'),
                   periodic_replay_valid=audit.get('periodic_replay', {}).get('valid'),
                   solver_config=info.get('solver_config'))
    except subprocess.TimeoutExpired:
        row.update(status='EXTERNAL_TIMEOUT', error='CLI exceeded budget plus 180 seconds')
    except Exception as exc:
        row.update(status='EXECUTION_ERROR', error=str(exc), traceback=traceback.format_exc())
    row.update(elapsed=time.perf_counter()-begun, completed_at=now())
    write_json(OUT / 'rows' / f'{case}_m{model}.json', row)
    print(json.dumps(dict(event='done', **row), ensure_ascii=False), flush=True)
    return row


def main():
    if (OUT / 'manifest.json').exists():
        raise RuntimeError('Refusing to overwrite an existing run')
    for folder in ('runs', 'logs', 'rows'):
        (OUT / folder).mkdir(exist_ok=True)
    import psutil
    memory = psutil.virtual_memory()
    workers = min(8, max(1, (os.cpu_count() or 2)//2), max(1, int(memory.available//(3*1024**3))))
    manifest = dict(started_at=now(), interpreter=sys.executable, python=sys.version,
                    commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    source_status=subprocess.check_output(['git', 'status', '--porcelain', '--', 'models', 'cli'],
                                                        cwd=ROOT, text=True, encoding='utf-8'),
                    models=[5, 6, 7, 8], cases=[f'S{i:02d}' for i in range(1, 10)],
                    exact_seconds=BUDGET, seed=42, threads_per_solve=1, workers=workers,
                    logical_cpus=os.cpu_count(), available_memory_bytes=memory.available,
                    external_initial_solution=False, batch_count_domain='unchanged_from_original_case',
                    planned=36, completed=0)
    write_json(OUT / 'manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    rows = []
    summarize(rows)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, model, f'S{i:02d}') for i in range(1, 10) for model in range(5, 9)]
        for future in as_completed(futures):
            rows.append(future.result())
            summarize(rows)
            manifest['completed'] = len(rows)
            write_json(OUT / 'manifest.json', manifest)
    manifest['completed_at'] = now()
    write_json(OUT / 'manifest.json', manifest)
    print(json.dumps(dict(event='complete', completed=len(rows), report=str(OUT / 'report.md'))), flush=True)


if __name__ == '__main__':
    main()
