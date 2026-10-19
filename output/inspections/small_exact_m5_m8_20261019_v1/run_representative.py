"""Continue the user-selected S01/S04/S07 scope, reusing completed runs."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CASES = ('S01', 'S04', 'S07')
spec = importlib.util.spec_from_file_location('probe', OUT / 'run_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def selected_rows():
    return sorted((read(path) for path in (OUT/'rows').glob('*.json')
                   if read(path).get('case') in CASES), key=lambda row: (row['case'], row['model']))


def report():
    rows = selected_rows()
    extras = [read(path) for path in (OUT/'rows').glob('*.json') if read(path).get('case') not in CASES]
    probe.write_json(OUT/'summary.json', rows)
    lines = ['# M5–M8 正式小规模：12 个代表组合', '',
             f'按用户指定范围：S01（7 顾客）、S04（10 顾客）、S07（12 顾客），各跑 M5–M8。完成 {len(rows)}/12。',
             '每例 300 秒总求解预算，单线程，seed=42，expanded 表达，完整输入 K 域。',
             '直接 Gurobi，无外部初解、定制算法、外部下界证书或人为缩小 K 域。',
             'M5 原生目标是距离，M6–M8 原生目标是成本，数值不能跨这两类直接比较。', '',
             '| 模型 | 已完成 | 审计有效可行解 | 已证明最优 | 已证明不可行 | 无有效解 |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for model in range(5, 9):
        group = [row for row in rows if row['model'] == model]
        lines.append(f"| M{model} | {len(group)}/3 | {sum(r['valid'] for r in group)} | "
                     f"{sum(r['proven_optimal'] for r in group)} | {sum(r['proven_infeasible'] for r in group)} | "
                     f"{sum(not r['valid'] for r in group)} |")
    lines += ['', '| 算例 | 顾客 | 模型 | K域 | 状态 | 有效 | 最优 | 目标 | 下界 | gap | K | 建模秒 | MIP秒 | 墙钟秒 |',
              '| --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    def num(value):
        return '—' if value is None else f'{value:.4f}'
    by_key = {(row['case'], row['model']): row for row in rows}
    for case, customers in zip(CASES, (7, 10, 12)):
        for model in range(5, 9):
            row = by_key.get((case, model))
            if row is None:
                lines.append(f'| {case} | {customers} | M{model} | — | 待完成 | — | — | — | — | — | — | — | — | — |')
                continue
            gap = '—' if row.get('gap') is None else f"{100*row['gap']:.2f}%"
            lines.append(f"| [{case}]({row['run_dir']}/result.json) | {customers} | M{model} | {row['counts']} | "
                         f"{row['status']} | {row['valid']} | {row['proven_optimal']} | {num(row.get('objective'))} | "
                         f"{num(row.get('bound'))} | {gap} | {row.get('selected_k') or '—'} | "
                         f"{num(row.get('build_time'))} | {num(row.get('mip_time'))} | {num(row.get('elapsed'))} |")
    lines += ['', '限时无有效解不等于证明不可行；未证明最优的可行解仍是上界。',
              '原始 result.json、输入哈希、源码快照、日志及两周期独立重放审计保存在每个 runs 目录。',
              '这只覆盖 12 个代表组合，不是全部 36 个正式小规模组合。', '', '## 范围调整前的额外运行', '',
              f'另有 {len(extras)} 个 S02 组合已完成，保留原始记录，不计入上述 12 个目标组合。',
              'S02/M8 在范围调整时停止，没有完整结果，不作可行性或最优性判断。', '']
    for row in sorted(extras, key=lambda item: (item['case'], item['model'])):
        lines.append(f"- {row['case']}/M{row['model']}: {row['status']}, valid={row['valid']}, "
                     f"proven_optimal={row['proven_optimal']}。")
    (OUT/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return rows


def main():
    manifest_path = OUT/'manifest.json'
    if not (OUT/'manifest_initial_scope.json').exists():
        shutil.copy2(manifest_path, OUT/'manifest_initial_scope.json')
    manifest = read(manifest_path)
    current_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if manifest['commit'] != current_commit:
        raise RuntimeError('Source commit changed during the probe')
    probe.write_json(OUT/'scope_change.json', dict(changed_at=now(), source='explicit_user_selection',
        original_cases=[f'S{i:02d}' for i in range(1, 10)], selected_cases=list(CASES),
        original_planned=36, selected_planned=12, superseded_active_run='S02_m8',
        completed_extra_runs=['S02_m5', 'S02_m6', 'S02_m7']))
    manifest.update(cases=list(CASES), planned=12, workers=1, resumed_at=now(),
                    scope='user_selected_representatives', previous_planned=36)
    manifest.pop('completed_at', None)
    rows = report()
    manifest['completed'] = len(rows)
    probe.write_json(manifest_path, manifest)
    for case in CASES:
        for model in range(5, 9):
            if (OUT/'rows'/f'{case}_m{model}.json').exists():
                continue
            if (OUT/'runs'/f'{case}_m{model}').exists():
                raise RuntimeError('Incomplete prior target directory requires separate recovery')
            probe.run_one(model, case)
            rows = report()
            manifest['completed'] = len(rows)
            probe.write_json(manifest_path, manifest)
    manifest.update(completed_at=now(), completed=len(rows))
    probe.write_json(manifest_path, manifest)
    print(json.dumps(dict(event='complete_representatives', completed=len(rows), report=str(OUT/'report.md'))), flush=True)


if __name__ == '__main__':
    main()
