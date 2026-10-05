import hashlib
import json

import pytest

from tools.data.restore_benchmark_sources import process_phase


def phase(tmp_path):
    (tmp_path/'source_snapshot').mkdir()
    source = tmp_path/'source_snapshot/main.py'
    source.write_bytes(b'print(1)\r\n')
    hashes = {'main.py':hashlib.sha256(source.read_bytes()).hexdigest()}
    (tmp_path/'manifest.json').write_text(json.dumps({'source_sha256':hashes}))
    (tmp_path/'run').mkdir()
    (tmp_path/'run/manifest.json').write_text(json.dumps({'source_sha256':hashes}))
    return source


def test_restoration_preserves_exact_bytes_and_rejects_changed_existing_copy(tmp_path):
    source = phase(tmp_path)
    assert process_phase(tmp_path)['source_references']==1
    assert not (tmp_path/'run/source_snapshot').exists()
    assert process_phase(tmp_path,True)['restored_files']==1
    target = tmp_path/'run/source_snapshot/main.py'
    assert target.read_bytes()==source.read_bytes()
    target.write_bytes(b'changed')
    with pytest.raises(ValueError,match='Refusing to replace'):
        process_phase(tmp_path,True)


def test_different_run_manifest_cannot_be_restored_from_shared_source(tmp_path):
    phase(tmp_path)
    (tmp_path/'run/manifest.json').write_text(json.dumps({'source_sha256':{'main.py':'0'*64}}))
    with pytest.raises(ValueError,match='differs from phase'):
        process_phase(tmp_path,True)
