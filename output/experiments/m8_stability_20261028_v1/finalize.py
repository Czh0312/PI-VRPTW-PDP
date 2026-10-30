"""Record actual final test counts and freeze experiment evidence hashes."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from summarize import main as summarize

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def main():
    xml_path=HERE/'validation_evidence.xml'
    recheck_path=HERE/'validation_evidence_domain_recheck.xml'

    def junit(path):
        root=ET.parse(path).getroot()
        suites=list(root.iter('testsuite'))
        actual={key:sum(int(suite.attrib.get(key,0)) for suite in suites)
                for key in ['tests','failures','errors','skipped']}
        actual['passed']=actual['tests']-actual['failures']-actual['errors']-actual['skipped']
        states={(case.attrib['classname'],case.attrib['name']):
                ('failed' if case.find('failure') is not None else
                 'error' if case.find('error') is not None else
                 'skipped' if case.find('skipped') is not None else 'passed')
                for case in root.iter('testcase')}
        return actual,states

    initial,states=junit(xml_path)
    recheck,rechecked_states=junit(recheck_path)
    assert initial==dict(tests=125,failures=3,errors=0,skipped=0,passed=122),initial
    assert recheck==dict(tests=3,failures=0,errors=0,skipped=0,passed=3),recheck
    assert set(rechecked_states)=={key for key,state in states.items() if state=='failed'}
    states.update(rechecked_states)
    assert len(states)==125 and set(states.values())=={'passed'}
    counts=dict(tests=125,failures=0,errors=0,skipped=0,passed=125)
    unit=read(HERE/'unit_validation.json')
    assert sum(run['passed'] for run in unit['invocations'])==unit['distinct_passed_tests']
    slot_replay=read(HERE/'original_slot_replay.json')
    validation=dict(
        full_repository_suite_run=False,unit_validation=unit,
        final_selected_scope='output/experiments/m8_stability_20261028_v1/test_evidence.py',
        final_evidence_counts=counts,
        evidence_test_invocations=[dict(scope='Entire evidence module',counts=initial,junit_sha256=sha(xml_path)),
            dict(scope='-k rederive_original_all_K',counts=recheck,deselected=122,junit_sha256=sha(recheck_path))],
        resolved_validation_issue='Three initial failures were PermissionError from os.replace in the sandbox temporary '
            'directory while saving fresh domain certificates. The solver completed. Only the output location was '
            'changed to the workspace, and only the three affected tests were rerun; all passed.',
        total_distinct_passed_tests=unit['distinct_passed_tests']+counts['passed'],
        total_failed=counts['failures'],total_errors=counts['errors'],total_skipped=counts['skipped'],
        solver_preflight='READY before solver work and final evidence validation; actual integer optimization passed',
        final_preflight_sha256=sha(HERE/'preflight_final.txt'),
        original_untrimmed_control_patterns_reproved=slot_replay['independently_reproved_unique_patterns'],
        benchmark_counts=dict(holdout=18,exploratory_pilot=15),
        tested_invariants=['Identical paired U/L and original K domain',
            'Frozen driver, production source and physical/economic input hashes',
            'Independent original-model business/cost audit and two-cycle replay',
            'Retained K clocks use identical seconds and subdivisions',
            'Native status preserved and certificate labels separated',
            'Original all-K cost-competitive K/N endpoints re-solved',
            'Every conditional task-mask screening bound recomputed',
            'Complete final master with every no-good and conditional cost cut reconstructed and bound re-proved',
            'Every distinct S05 certificate pattern re-solved with original untrimmed trip slots'])
    save(HERE/'validation.json',validation)
    summarize()
    report=HERE/'report.md'
    extra=(f"\n实际选定验证：算法与可行性测试 {unit['distinct_passed_tests']} 项通过；"
           f"证据测试首轮 122 项通过、3 项失败（沙盒临时目录保存文件时重命名被拒绝）；"
           f"仅将复核输出改到项目目录后，复测这 3 项全部通过，另 122 项没有重复运行。"
           f"最终合计 {validation['total_distinct_passed_tests']} 项通过，"
           f"最终未通过 {validation['total_failed']}、错误 {validation['total_errors']}、跳过 {validation['total_skipped']}。"
           f"S05 的 {slot_replay['independently_reproved_unique_patterns']} 个不同证书组合还使用原始车次槽位重新求解验证。\n")
    report.write_text(report.read_text(encoding='utf-8')+extra,encoding='utf-8')
    files=[path for path in HERE.rglob('*') if path.is_file() and '__pycache__' not in path.parts
           and path.name not in ('evidence_manifest.json','frozen_evidence.zip') and path.suffix!='.tmp']
    save(HERE/'evidence_manifest.json',dict(
        evidence_files={str(path.relative_to(HERE)):sha(path) for path in sorted(files)},
        scope='Final report, frozen plans/algorithms, all 33 trial logs/results, inputs and executed test evidence. '
              'Production source and original external input hashes are additionally saved in each trial manifest.'))
    print(json.dumps(validation,ensure_ascii=False))


if __name__=='__main__':
    main()
