"""Preserve paused raw evidence without marking an incomplete study as finished."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import zipfile

HERE=Path(__file__).resolve().parent


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def main():
    pause=json.loads((HERE/'pipeline_state.json').read_text(encoding='utf-8'))
    if pause['status']!='paused_by_user':
        raise ValueError('Checkpoint is only for the recorded user-paused state')
    for name in ('s01','representatives'):
        folder=HERE/name
        progress=json.loads((folder/'progress.json').read_text(encoding='utf-8'))
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        files={p.relative_to(folder).as_posix():p for group in ('inputs','cases','runs')
            for p in sorted((folder/group).rglob('*')) if p.is_file() and p.suffix!='.tmp'}
        hashes={path:sha(source.read_bytes()) for path,source in files.items()}
        target=folder/'paused_checkpoint.zip'
        if target.exists():
            raise ValueError(f'Refusing to overwrite checkpoint: {target}')
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for path,source in files.items():
                archive.write(source,path)
        with zipfile.ZipFile(target) as archive:
            assert set(archive.namelist())==set(files)
            assert all(sha(archive.read(path))==value for path,value in hashes.items())
        metadata=dict(kind='user_paused_checkpoint_not_a_completed_study_package',
            captured_at=datetime.now(timezone.utc).isoformat(),study=name,
            study_complete=progress['state']=='COMPLETED',progress=progress,
            manifest_sha256=sha((folder/'manifest.json').read_bytes()),
            archive=target.name,archive_sha256=sha(target.read_bytes()),files=hashes,
            source_archive_hashes=manifest['source_archive_hashes'],
            restore='Verify all archive hashes, then extract this checkpoint and source_current.zip into the study directory. Frozen paths retain the original location. Do not auto-resume; STOP and PIPELINE_STOP remain effective.',
            limitation='Pending calls were not run. An interrupted call is not evidence of model infeasibility or algorithm failure.')
        (folder/'paused_checkpoint.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(study=name,files=len(files),bytes=target.stat().st_size,
            study_complete=metadata['study_complete'],valid=True)),flush=True)


if __name__=='__main__':
    main()
