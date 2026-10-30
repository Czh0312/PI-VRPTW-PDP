"""Archive the three unreferenced superseded suites, with verified restoration.

The retirement list is explicit. Active source/configuration references block
retirement; historical outputs and prose remain untouched. Every original byte
is checked inside the ZIP before removal, and can be restored without overwrite.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from tools.configuration.loader import PROJECT_ROOT

TARGETS=('data/model6/cutoff_v1_20261005','data/model6/static_v2_full_20261004',
         'data/model6/static_v2_lade_20261004')
DESTINATION=PROJECT_ROOT/'archive/datasets'


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def active_references():
    blocked, allowed=[],[]
    for prefix in ('data','tools','experiments','cli','models','tests','config'):
        for path in sorted((PROJECT_ROOT/prefix).rglob('*')):
            if not path.is_file() or path.suffix not in ('.json','.py','.toml','.yaml','.yml','.sh'):
                continue
            if any((PROJECT_ROOT/r) in path.parents for r in TARGETS) or path.resolve()==Path(__file__).resolve():
                continue
            for number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
                if not any(r in line for r in TARGETS):
                    continue
                row=dict(file=path.relative_to(PROJECT_ROOT).as_posix(),line=number,text=line.strip())
                # This is a builder output destination, not an input read.
                if row['file']=='tools/data/build_model6_static_suite.py' and "parser.add_argument('--output'," in line:
                    allowed.append(row)
                else:
                    blocked.append(row)
    return blocked,allowed


def retire():
    blocked,allowed=active_references()
    if blocked:
        raise ValueError(f'Active references block retirement: {blocked}')
    if (DESTINATION/'retired_20261015.zip').exists():
        raise ValueError('Archive already exists; refusing to replace it')
    originals={}
    for folder in TARGETS:
        root=PROJECT_ROOT/folder
        if not root.is_dir():raise ValueError(f'Missing retirement target: {folder}')
        for path in sorted(root.rglob('*')):
            if path.is_symlink():raise ValueError('Do not archive symlinks')
            if path.is_file():originals[path.relative_to(PROJECT_ROOT).as_posix()]=dict(
                sha256=digest(path.read_bytes()),bytes=path.stat().st_size)
    DESTINATION.mkdir(parents=True,exist_ok=True)
    archive=DESTINATION/'retired_20261015.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in originals:z.write(PROJECT_ROOT/name,name)
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist())!=set(originals):raise ValueError('Archive inventory mismatch')
        for name,record in originals.items():
            if digest(z.read(name))!=record['sha256']:raise ValueError(f'Archive mismatch: {name}')
            if digest((PROJECT_ROOT/name).read_bytes())!=record['sha256']:raise ValueError(f'Concurrent edit: {name}')
    report=dict(date='2026-10-15',targets=list(TARGETS),files=originals,
        file_count=len(originals),original_bytes=sum(r['bytes'] for r in originals.values()),
        archive_bytes=archive.stat().st_size,archive_sha256=digest(archive.read_bytes()),
        active_input_references=blocked,allowed_output_only_references=allowed,
        historical_outputs_and_docs='Preserved; restore this archive to rerun old paths',
        decision='Superseded suites with no active input references; retain used legacy suites and original sources')
    (DESTINATION/'retired_20261015.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    # All targets, all archived bytes and the live input scan have passed.
    for folder in TARGETS:shutil.rmtree(PROJECT_ROOT/folder)
    (DESTINATION/'README.md').write_text('# 已退役数据集\n\n'
        '本目录保存2026-10-15清理的三个旧版本，ZIP逐文件SHA-256已核验。旧输出和文档未改写。'
        '正式数据集位于data/formal_benchmark27_v1；仍被测试或历史实验入口使用的旧数据保留原位置。\n\n'
        '恢复原路径：`.venv/bin/python -m tools.data.retire_unused_datasets --restore`。'
        '恢复器先验证全部内容，拒绝覆盖任何不同的现有文件。清单见retired_20261015.json。\n',encoding='utf-8')
    return {k:v for k,v in report.items() if k!='files'}


def restore():
    report=json.loads((DESTINATION/'retired_20261015.json').read_text())
    archive=DESTINATION/'retired_20261015.zip'
    if digest(archive.read_bytes())!=report['archive_sha256']:raise ValueError('Archive checksum mismatch')
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist())!=set(report['files']):raise ValueError('Archive inventory mismatch')
        for name,record in report['files'].items():
            target=(PROJECT_ROOT/name).resolve()
            if not any((PROJECT_ROOT/r).resolve() in target.parents for r in TARGETS):
                raise ValueError('Archive path outside explicit retirement targets')
            if digest(z.read(name))!=record['sha256']:raise ValueError('Archived file mismatch')
            if target.exists() and digest(target.read_bytes())!=record['sha256']:
                raise ValueError(f'Refusing to overwrite different content: {name}')
        for name in report['files']:
            target=PROJECT_ROOT/name
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
    return dict(restored=len(report['files']),targets=list(TARGETS))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--retire',action='store_true');modes.add_argument('--restore',action='store_true')
    args=parser.parse_args()
    print(json.dumps(restore() if args.restore else retire(),ensure_ascii=False))
