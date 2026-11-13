"""A candidate experiment must freeze its actual source tree after renames."""
from pathlib import Path
import zipfile

from experiments.runs import compare_joint_refinement as driver


def test_candidate_snapshot_replaces_old_source_modules(tmp_path, monkeypatch):
    current = tmp_path/'current'
    folders = ('models', 'tools', 'cli', 'experiments')
    for folder in folders:
        (current/folder).mkdir(parents=True)
        (current/folder/'current.py').write_text('VERSION = "new"\n')
    for folder in ('data', 'config', 'archive'):
        (current/folder).mkdir()
    baseline = tmp_path/'baseline.zip'
    with zipfile.ZipFile(baseline, 'w') as archive:
        for folder in folders:
            archive.writestr(folder+'/removed.py', 'VERSION = "old"\n')
        archive.writestr('support.json', '{"retain": true}')
    monkeypatch.setattr(driver, 'ROOT', current)
    frozen = driver.snapshots(tmp_path/'frozen', baseline)
    for folder in folders:
        assert (frozen['baseline']/folder/'removed.py').is_file()
        assert not (frozen['candidate']/folder/'removed.py').exists()
        assert (frozen['candidate']/folder/'current.py').read_text() == 'VERSION = "new"\n'
    assert (frozen['candidate']/'support.json').read_text() == '{"retain": true}'
    assert (frozen['candidate']/'data').resolve() == current/'data'
