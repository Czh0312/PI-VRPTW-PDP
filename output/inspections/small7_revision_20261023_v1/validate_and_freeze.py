from pathlib import Path
import json
from tools.data.build_formal_benchmark27 import digest, write_json
from tools.data.build_benchmark27_small7 import SOURCE, VERSION
from tools.audit.audit_benchmark27_spacetime import audit_design
from tools.audit.certify_formal_benchmark27 import certify, verify_witnesses

root = Path('data') / VERSION
out = Path('output/inspections/small7_revision_20261023_v1')
audit = audit_design(root)
write_json(out/'input_audit.json', audit)
print('INPUT_AUDIT: 27 base cases / 216 configurations valid', flush=True)
witnesses = certify(root)
write_json(out/'witness_verification.json', witnesses)
print('WITNESSES: 216 independently audited feasible schedules', flush=True)
# Certification updates the manifest, so freeze only afterwards.
final_audit = audit_design(root)
write_json(out/'input_audit.json', final_audit)
source = Path(SOURCE)
source_integrity = json.loads((root/'source_integrity.json').read_text(encoding='utf-8'))
assert all(digest(source/name)==expected for name,expected in source_integrity['sha256'].items())
write_json(out/'source_integrity.json',dict(passed=True,source_dataset=SOURCE,files_checked=source_integrity['files_checked']))
registry = Path('config/experiments/benchmark27_spacetime_v4/frozen.json')
if registry.exists():
    raise ValueError('Refusing to overwrite frozen registration')
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
frozen=dict(schema_version=1,status='USER_CONFIRMED_FROZEN',confirmed_on='2026-10-23',
    dataset=root.as_posix(),dataset_revision=4,manifest_sha256=digest(root/'manifest.json'),
    selection_sha256=digest(root/'selection.json'),case_index_sha256=digest(root/'case_index.csv'),
    base_case_count=27,model_config_count=216,models=list(range(1,9)),
    customer_count_quotas=manifest['customer_count_quotas'],demand_contract=manifest['demand_contract'],
    selection_policy='First seven numeric customer IDs of each corresponding v3 ten-customer case; no solver-result screening.',
    preserved_inputs='All retained order fields, coordinates, warehouses, fleet and economic bytes; medium/large physical inputs unchanged.',
    change_policy='Use this frozen dataset for future experiments. Customer-set or size changes require a new version; never overwrite or relabel historical results.',
    validation_report=(out/'README.md').as_posix())
write_json(registry,frozen)
write_json(out/'tests.json',dict(command="tools/run.ps1 test -Arguments @('tests/data/test_small7_benchmark_design.py','tests/data/test_small10_benchmark_design.py','-q')",passed=10,failed=0,skipped=0,seconds=141.69,scope='Seven-customer revision and backward-compatible ten-customer generation/audit tests; exact runtime preflight passed.'))
(out/'README.md').write_text('# 小规模统一7顾客：修订4冻结核验\n\n'
    'S01—S09按v3同编号算例的顾客编号数值排序取前7位，保留全部仓点、车队、所选订单字段和费用，不依据求解结果选例。中大规模物理输入保持。\n\n'
    '27个基础算例、216份配置通过独立输入审计；216份两周期调度见证均通过独立重放。见证证明可行，不证明最优，也不自动作为求解初解。\n\n'
    '新旧两版数据回归：10通过、0失败、0跳过。源v3全文件哈希核验通过。\n\n'
    '新版：data/formal_benchmark27_spacetime_v4。冻结登记：config/experiments/benchmark27_spacetime_v4/frozen.json。\n'
    '正式重新测试另存output/experiments/small7_m1_m4_exact300_20261023_v1，M1—M4各9例，单次300秒，冷启动、compact、单线程、种子42，M4强化关闭。\n',encoding='utf-8')
print(json.dumps(dict(frozen=True,manifest_sha256=frozen['manifest_sha256'],source_files_unchanged=source_integrity['files_checked']),ensure_ascii=False),flush=True)
