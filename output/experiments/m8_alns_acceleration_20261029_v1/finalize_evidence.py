"""Archive selected validation, both immutable solvers, inputs and run evidence."""
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from run import HERE, ROOT, read, save, sha
from analyze import verify_plan


def junit(path):
    suites = list(ET.parse(path).getroot().iter('testsuite'))
    counts = {key: sum(int(s.get(key, 0)) for s in suites)
              for key in ['tests', 'failures', 'errors', 'skipped']}
    assert counts['failures'] == counts['errors'] == counts['skipped'] == 0, counts
    return dict(passed=counts['tests'], failed=0, errors=0, skipped=0,
                junit=str(path), junit_sha256=sha(path))


plan = verify_plan('confirmatory', 'run.py')
verify_plan('pilot', 'pilot_driver.py')
candidate_tests = junit(HERE / 'validation_candidate.xml')
integration_tests = junit(HERE / 'validation_integration.xml')
assert candidate_tests['passed'] == 22
validation = dict(candidate=candidate_tests, integration=integration_tests,
    total_selected_passed=candidate_tests['passed']+integration_tests['passed'],
    failed=0, errors=0, skipped=0, full_repository_suite_run=False,
    candidate_scope=['output/experiments/m8_alns_acceleration_20261029_v1/test_candidate.py'],
    integration_scope=['tests/routing/test_model8_evaluation_pool.py',
        'tests/routing/test_model8_fast_retiming.py', 'tests/routing/test_model8_decision_grid.py',
        'tests/routing/test_productive_batches.py',
        'tests/routing/test_solver_isolation.py::test_solver_runs_without_importing_the_other_algorithm[alns]'])
initial = ET.parse(HERE / 'validation_integration_before_promotion.xml').getroot()
initial_suites = list(initial.iter('testsuite'))
validation['pre_promotion_attempt'] = dict(
    passed=sum(int(s.get('tests', 0))-int(s.get('failures', 0))-int(s.get('errors', 0))-int(s.get('skipped', 0)) for s in initial_suites),
    failed=sum(int(s.get('failures', 0)) for s in initial_suites),
    explanation='Integration was started after a Windows path-key promotion check failed. '
                'New feature checks ran against the unchanged old evaluator. Path handling was '
                'corrected, the released evaluator was installed, and the complete selection rerun.')
save(HERE / 'validation.json', validation)
promotion = read(HERE / 'promotion.json')
assert promotion['default_enabled']
assert sha(Path(promotion['production_file'])) == promotion['after_sha256']
promotion['selected_integration_validation'] = 'passed'
promotion['selected_integration_tests'] = integration_tests['passed']
save(HERE / 'promotion.json', promotion)
inputs = set()
small_prior = read(ROOT / 'output/experiments/m8_all_small_rerun_20261029_v1/plan.json')
for raw, digest in small_prior['input_hashes'].items():
    path = Path(raw)
    if path.is_relative_to(ROOT / 'data'):
        assert sha(path) == digest, raw
for case in [f'S{i:02}' for i in range(1, 10)]+['M01', 'L01']:
    size = {'S': 'small', 'M': 'medium', 'L': 'large'}[case[0]]
    path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/{size}/{case}/case.json'
    inputs.add(path)
    config = read(path)
    for key in ['data', 'orders', 'routing_config', 'operating_policy', 'economic_config',
                'dataset_metadata', 'parameter_contract', 'batch_count_certificate']:
        if isinstance(config.get(key), str):
            referenced = Path(config[key])
            inputs.add(referenced if referenced.is_absolute() else ROOT / referenced)
    if case.startswith('S'):
        inputs.add(ROOT / f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/summary.json')
save(HERE / 'input_and_reference_hashes.json', {str(p): sha(p) for p in sorted(inputs, key=str)})
report = HERE / 'report.md'
text = report.read_text(encoding='utf-8')
text += ('\n本轮专项候选验证 **'+str(candidate_tests['passed'])+' passed**，生产入口及相关回归所选范围 **'
         +str(integration_tests['passed'])+' passed**；合计 '+str(validation['total_selected_passed'])
         +' 个所选测试通过，0 失败、0 错误、0 跳过，未运行全仓库测试。运行器预检 READY，实际 Gurobi 11.0.3 整数优化通过。\n\n'
         '首次集成验证误在 Windows 路径校验导致发布中止后仍启动，因此针对旧默认评价器运行，'
         '有7项新增特性检查失败、67项通过；原JUnit单独保留。修正路径后完成发布，并重跑完整所选范围通过。\n\n'
         '已将按规模保护的发布版评价器替换到默认 M8 ALNS 入口。快评只在不超过9单时启用，'
         '超过9单执行原评价路径；正式短预算质量验证覆盖的是七单算例。'
         '原全规模候选在 M01 两组、L01 一组上出现成本回退，因此没有对中大例全量启用。'
         '原评价器完整保存在 sources/baseline/，生产文件与 sources/released/ 的 SHA256 一致。'
         '发布版再次对全部 8,366 次冻结候选回放，完整返回结果仍与原版相同。用户原有其他修改没有回退。'
         '本次生产改动为 evaluator.py、相关 README 和独立回归测试；精确算法及业务输入没有修改。\n')
report.write_text(text, encoding='utf-8')
with zipfile.ZipFile(HERE / 'frozen_sources_inputs.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted((HERE / 'sources').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix == '.py':
            archive.write(path, path.relative_to(HERE).as_posix())
    for path in sorted(inputs, key=str):
        archive.write(path, 'inputs_snapshot/' + path.relative_to(ROOT).as_posix())
    for path in [ROOT / 'tests/routing/test_model8_evaluation_pool.py',
                 ROOT / 'models/routing/model_8/alns/README.md']:
        archive.write(path, 'promoted/' + path.relative_to(ROOT).as_posix())
    for name in ['run.py', 'pilot_driver.py', 'prepare.py', 'test_candidate.py', 'paired_replay.py',
                 'paired_replay_wall_only.py', 'analyze.py', 'promote.py', 'make_release.py',
                 'replay_release.py', 'finalize_evidence.py']:
        archive.write(HERE / name, 'experiment/' + name)
files = [p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts
         and p.name != 'evidence_manifest.json']
manifest = {p.relative_to(HERE).as_posix(): sha(p) for p in sorted(files)}
save(HERE / 'evidence_manifest.json', dict(file_count=len(manifest), files=manifest))
assert all(sha(HERE / name) == digest for name, digest in manifest.items())
print(dict(validation=validation, default_enabled=True, evidence_files=len(manifest)), flush=True)
