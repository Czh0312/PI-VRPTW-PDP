"""Compress raw review artifacts with verified, non-overwriting restoration."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def pack(root):
    root=Path(root).resolve()
    if not (root/'summary.json').is_file():
        raise ValueError('Only completed studies can be packed')
    archive=root/'artifacts.zip'
    if archive.exists():raise ValueError('Refusing to replace existing archive')
    files={p.relative_to(root).as_posix():digest(p.read_bytes())
        for name in ('frozen_root','runs') for p in (root/name).rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    if not files:raise ValueError('No artifacts to pack')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):z.write(root/name,name)
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist())==set(files)
        for name,h in files.items():
            if digest(z.read(name))!=h or digest((root/name).read_bytes())!=h:
                raise ValueError(f'Artifact changed during packing: {name}')
    (root/'packed_artifacts.json').write_text(json.dumps(dict(
        archive_sha256=digest(archive.read_bytes()),files=files,
        excluded_generated_caches=['__pycache__','*.pyc'],
        restore_command=f'.venv/bin/python -m tools.audit.pack_benchmark_review {root} --restore'),indent=2)+'\n')
    for name in ('frozen_root','runs'):shutil.rmtree(root/name)
    return dict(files=len(files),archive_bytes=archive.stat().st_size)


def restore(root):
    root=Path(root).resolve()
    report=json.loads((root/'packed_artifacts.json').read_text())
    archive=root/'artifacts.zip'
    if digest(archive.read_bytes())!=report['archive_sha256']:raise ValueError('Archive changed')
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist())!=set(report['files']):raise ValueError('Inventory changed')
        for name,h in report['files'].items():
            target=(root/name).resolve()
            if root not in target.parents or Path(name).parts[0] not in ('frozen_root','runs'):
                raise ValueError('Unsafe archived path')
            if digest(z.read(name))!=h:raise ValueError('File checksum mismatch')
            if target.exists() and digest(target.read_bytes())!=h:raise ValueError(f'Refusing to overwrite {name}')
        for name in report['files']:
            target=root/name
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
    return dict(restored=len(report['files']))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('study');parser.add_argument('--restore',action='store_true')
    args=parser.parse_args();print(json.dumps(restore(args.study) if args.restore else pack(args.study)))
