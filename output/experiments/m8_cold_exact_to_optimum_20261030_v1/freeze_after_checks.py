"""Finalize recorder metadata fixes before any actual benchmark worker starts."""
import xml.etree.ElementTree as ET
import bench

plan = bench.read(bench.HERE/'plan.json')
assert not (bench.HERE/'runs').exists(), 'Cannot revise a protocol after benchmark jobs start'
assert bench.source_hashes()==plan['source_hashes']
assert all(bench.sha(path)==digest for path,digest in plan['input_hashes'].items())
xml = ET.parse(bench.HERE/'protocol_checks.xml').getroot()
suites = list(xml.iter('testsuite'))
assert sum(int(s.get('tests',0)) for s in suites)==3
assert all(int(s.get(k,0))==0 for s in suites for k in ('failures','errors','skipped'))
bench.save(bench.HERE/'plan_prevalidation.json',plan)
plan.update(driver_hashes={name:bench.sha(bench.HERE/name) for name in ('bench.py','protocol.py')},
    protocol_validated_before_any_benchmark_worker=True,
    protocol_checks_sha256=bench.sha(bench.HERE/'protocol_checks.xml'),
    pre_start_revision='Recorder handles native infinite limits as JSON null; test logs use ASCII project-relative path',
    finalized_utc=bench.utc_now())
bench.save(bench.HERE/'plan.json',plan)
print('Protocol frozen after 3 passed, 0 failed/errors/skipped; no benchmark jobs have run.')
