"""Export and verify an immutable, explicitly partial spacetime study delivery.

Use the project runtime launcher on Windows. This exporter never changes the
registered study, solves a model, or converts unfinished attempts into results.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def included(path):
    return not (set(path.parts) & {'__pycache__', '.venv', '.git', '.pytest_cache'}
        or path.name in {'driver.lock', '.gitignore', 'STOP', 'AGENTS.md', 'project_runtime.py'}
        or path.name.endswith(('.local.ps1', '.pyc', '.tmp', '.lic'))
        or path.name.startswith('.env'))


def verify_archive(snapshot):
    """Check every archived byte, the frozen registration and completion hashes."""
    snapshot = Path(snapshot).resolve()
    checks = read(snapshot/'archive.sha256.json')
    for name, expected in checks.items():
        if digest((snapshot/name).read_bytes()) != expected:
            raise ValueError(f'Archive checksum mismatch: {name}')
    expected_files = read(snapshot/'files.sha256.json')
    if set(checks) != {'study_raw.zip'}:
        raise ValueError('Unsupported archive layout')
    with zipfile.ZipFile(snapshot/'study_raw.zip') as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('Duplicate archive names')
        if set(archive.namelist()) != set(expected_files):
            raise ValueError('Archive inventory mismatch')
        for name, expected in expected_files.items():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError(f'Unsafe archive path: {name}')
            if digest(archive.read(name)) != expected:
                raise ValueError(f'Archived file mismatch: {name}')
        manifest = json.loads(archive.read('manifest.json'))
        if expected_files['manifest.json'] != json.loads(archive.read('manifest.sha256.json'))['sha256']:
            raise ValueError('Frozen manifest mismatch')
        frozen_count = 0
        for group in ('source_hashes', 'input_hashes'):
            for name, expected in manifest[group].items():
                if expected_files.get(name) != expected:
                    raise ValueError(f'Frozen {group} mismatch: {name}')
                frozen_count += 1
        completions = 0
        for name in expected_files:
            if name.startswith('runs/') and name.endswith('/completion.json'):
                for relative, expected in json.loads(archive.read(name))['hashes'].items():
                    member = (Path(name).parent/relative).as_posix()
                    if expected_files.get(member) != expected:
                        raise ValueError(f'Original completed attempt changed: {member}')
                completions += 1
    for path in (snapshot/'study').rglob('*'):
        if path.is_file():
            name = path.relative_to(snapshot/'study').as_posix()
            if digest(path.read_bytes()) != expected_files.get(name):
                raise ValueError(f'Readable summary mismatch: {name}')
    return dict(valid=True, archived_files=len(expected_files), frozen_hashes_verified=frozen_count,
        completed_attempts_verified=completions, independent_replay_rerun=False,
        audit_scope='Byte/hash integrity of original independently audited solver records; no new solver run.')


def export(study, output, validation_logs=()):
    study, output = Path(study).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new snapshot directory; previous deliveries are immutable')
    if output.is_relative_to(study):
        raise ValueError('Snapshot must be outside the active study')
    manifest, progress = read(study/'manifest.json'), read(study/'progress.json')
    if digest((study/'manifest.json').read_bytes()) != read(study/'manifest.sha256.json')['sha256']:
        raise ValueError('Original registration changed')
    for group in ('source_hashes', 'input_hashes'):
        for name, expected in manifest[group].items():
            if digest((study/name).read_bytes()) != expected:
                raise ValueError(f'Original frozen hash mismatch: {name}')
    paths = sorted(p for p in study.rglob('*') if p.is_file() and included(p.relative_to(study)))
    attempts = sorted((study/'runs').glob('*/attempt_*'))
    unfinished = [p.relative_to(study).as_posix() for p in attempts if not (p/'completion.json').is_file()]
    completed = [read(p/'row.json') for p in attempts if (p/'completion.json').is_file()]
    resources = [r for r in completed if r['status'] in ('RESOURCE_LIMIT','SKIPPED_RESOURCE_LIMIT')]
    output.mkdir(parents=True)
    hashes = {}
    with zipfile.ZipFile(output/'study_raw.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in paths:
            name, payload = path.relative_to(study).as_posix(), path.read_bytes()
            hashes[name] = digest(payload)
            info = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
            if len(Path(name).parts) == 1 or Path(name).parts[0] == 'tables':
                target = output/'study'/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
    if (output/'study_raw.zip').stat().st_size >= 90*1024**2:
        raise ValueError('Archive exceeds this delivery limit; use separately registered split archives')
    write(output/'files.sha256.json', hashes)
    write(output/'archive.sha256.json', {'study_raw.zip':digest((output/'study_raw.zip').read_bytes())})
    for log in validation_logs:
        target = output/'validation'/Path(log).name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(log, target)
    summary = dict(schema_version=1, captured_at=datetime.now(timezone.utc).isoformat(),
        delivery_kind='PARTIAL_PROGRESS_SNAPSHOT', study=study.name, final_study_complete=False,
        source_commit=manifest['commit'], registration_sha256=hashes['manifest.json'],
        progress_as_recorded=progress, original_statuses=dict(Counter(r['status'] for r in completed)),
        unfinished_attempts_preserved=unfinished,
        resource_recovery_required=[{k:r[k] for k in ('id','case','model','method','budget','seed','status')} for r in resources],
        runtime_note='The coordinator was absent at capture; running entries in the original progress file are stale, not current live processes.',
        accounting_note='Resource failures, interruptions, pending jobs and conditional skips remain separate. Missing solutions never count as zero gap.',
        final_delivery_pending=True, archive_bytes=(output/'study_raw.zip').stat().st_size)
    write(output/'snapshot.json', summary)
    verification = verify_archive(output)
    write(output/'verification.json', verification)
    (output/'README.md').write_text(f'''# 27例八模型：阶段结果快照

这是按用户“先把结果推送到远程”的要求保存的阶段交付，**完整实验尚未完成**。
快照时间：{summary['captured_at']}。原始冻结注册 SHA256：`{summary['registration_sha256']}`。

已有精确最优证明 {progress['exact_optimal_cases']} 个模型算例；ALNS 已获得独立审计可行解
{progress['alns_feasible_cases']}/216 个模型算例。注册任务终态 {progress['terminal']}/{progress['planned']}
（含条件跳过），完整预算、种子和补跑仍待完成，不能据此宣称最终求解完成。
M5–M8 容器租金按完整780分钟周期收取 `1×N`，购置成本为0；其他费用系数保持不变。

[原始阶段报告](study/report.md) · [逐次运行](study/tables/runs.csv) ·
[ALNS GAP](study/tables/alns_gaps.csv) · [时间效率](study/tables/efficiency.csv) ·
[覆盖率](study/tables/coverage.csv) · [快照状态](snapshot.json) · [完整性核验](verification.json)

精确已证最优才计算最优GAP；其余仅报告可行参照差异与下界证书。失败和待运行项不计0GAP。
M1–M4 的距离目标与 M5–M8 的费用目标分别比较。各预算为独立冷启动，不能把重跑视为连续搜索。
求解墙钟时间包含并发竞争；内存派发等待单独保存在 `dispatch.json`，不算入单次求解预算。
原表保留已有逐次耗时；所有失败及重跑的累计成本还需在最终核对中汇总。

捕获时未发现实验控制器或工作进程；原 `progress.json` 中 RUNNING 为失联前状态。
归档保留 {len(unfinished)} 个没有 completion 标记的尝试及 {len(resources)} 个资源保护终态，
详见 `snapshot.json`。这些记录没有被覆盖，后续恢复/补跑将单独登记，自动跟进继续保留。
本次未重新执行所有解的回放；核验的是冻结哈希、原始完成记录及其中的审计证据。

`study_raw.zip` 包含冻结源码、全部输入、算例配置，以及当前原始解、双周期审计、轨迹、
求解日志和故障记录。ZIP内文件名相对原实验目录；排除了缓存、锁、本机运行脚本及许可证。
ZIP和每个文件的SHA256分别记录在 `archive.sha256.json` 和 `files.sha256.json`。
可读的 `study/` 与ZIP中的对应文件逐字节一致，后续实验不会改动这份快照。

## 核验与恢复

在仓库根目录用本项目的Python执行：

```text
python -m experiments.runs.export_spacetime_quality_snapshot verify --output {output.relative_to(study.parents[1]).as_posix()}
```

Windows本机按本地AGENTS.md通过 `tools/run.ps1` 的 `python` 模式执行上面的模块参数。
将ZIP解压到一个全新的目录即可恢复原始证据，勿覆盖活动实验目录。冻结配置保留原绝对路径
以维持哈希，若在其他机器复现求解，应另建工作副本并显式重定位配置路径及解释器；
重定位副本不能冒充原始字节哈希。使用归档中的 `driver.py`、`controller_io_adapter.py` 与
`source/`，保持 `manifest.json` 注册的模型、输入、全K域、预算、种子和租金设置。
已有解和审计不需要重新求解即可读取；长时实验和最终完整交付仍在后续跟进范围内。
''', encoding='utf-8')
    print(json.dumps(dict(output=str(output), summary=summary, verification=verification), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('export','verify'))
    parser.add_argument('--study')
    parser.add_argument('--output', required=True)
    parser.add_argument('--validation-log', action='append', default=[])
    args = parser.parse_args()
    if args.action == 'verify':
        print(json.dumps(verify_archive(args.output), ensure_ascii=False))
    elif args.study:
        export(args.study, args.output, args.validation_log)
    else:
        parser.error('export requires --study')


if __name__ == '__main__':
    main()
