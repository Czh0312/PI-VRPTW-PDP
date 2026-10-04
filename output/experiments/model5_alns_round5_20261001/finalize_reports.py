"""Verify all development evidence and append the exploratory protocol notes."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from tools.audit.benchmark_model5_alns import hashes
from tools.audit.summarize_model5_alns import compare, report
from configuration.loader import load_routing_case, apply_container_pool_size
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution


def main():
    manifest = json.loads((OUT/'benchmark_manifest.json').read_text())
    jobs = [j for j in manifest['jobs'] if j['split'] == 'development']
    before = [r for r in json.loads((OUT/'before/summary.json').read_text()) if r['split'] == 'development']
    problems, records = {}, []
    for n in range(1, 5):
        phase = f'pilot_{n:02d}'
        folder = OUT/phase
        frozen = json.loads((folder/'manifest.json').read_text())
        assert frozen['jobs'] == jobs and hashes(folder/'source') == frozen['source_sha256']
        assert json.loads((folder/'verification.json').read_text()) == dict(source_unchanged=True, inputs_unchanged=True)
        rows = json.loads((folder/'summary.json').read_text())
        assert len(rows) == 30
        assert compare(before, rows) == json.loads((OUT/(phase+'_comparison.json')).read_text())
        for row, job in zip(rows, jobs):
            assert all(row[k] == v for k, v in job.items())
            saved = json.loads((folder/row['result_file']).read_text())
            assert saved['job'] == job
            key = job['case'], job['pool']
            if key not in problems:
                config, fleet = load_routing_case(job['case'], use_pool_config=False)
                config, fleet = apply_container_pool_size(config, fleet, job['pool'])
                problems[key] = load_instance(config, fleet)
            audit = audit_solution(problems[key], saved['result'])
            assert audit == saved['independent_audit']
            assert audit['valid'] and audit['periodic_replay']['valid'] and row['valid']
        records.append(dict(phase=phase, registered_development_jobs=30, audited_and_replayed=30,
                            frozen_sources_and_statistics_match=True))
    (OUT/'development_verification.json').write_text(json.dumps(dict(checked_records=120, records=records), indent=2)+'\n')
    assert json.loads((OUT/'retention_decision.json').read_text())['default_adopted']
    data = json.loads((OUT/'comparison.json').read_text())
    report(OUT, data, 'before', 'after')
    path = OUT/'report.md'
    text = path.read_text().replace('## Material Passport',
        '**已通过注册的完整平均距离及平均 CV 门槛，采用候选 04 为默认下降策略。** '
        '[采用决策](retention_decision.json)。本轮属于探索性工程对照。\n\n## Material Passport', 1)
    text += ('\n## 探索过程与协议说明\n\n四个候选各运行完整 30 个开发作业，均保留改善和退步；'
        '选择候选 04 后才运行最终候选预留集。全部 120 个开发方案另经重审计及两周期回放，'
        '见 [开发核验](development_verification.json)。\n\n'
        '进度日志意外暴露了基线预留作业 34、35、36 的三个目标值，没有据此调整候选。'
        '因此不能称为完全盲化验证；这些情景也不是全新独立母例。'
        '详见 [筛查依据与协议记录](filter_reasoning.md) 和 [全部开发选择](selection.json)。\n\n'
        '[当前入口轨迹核验](final_modes_verification.json) · '
        '[完整第五轮文档](../../../docs/model5_alns_round5_20261001.md)\n')
    path.write_text(text)
    analysis = OUT/'final_analysis_source'
    for relative in ('tools/audit/benchmark_model5_alns.py', 'tools/audit/summarize_model5_alns.py',
                     'tools/audit/verify_model5_benchmark.py'):
        target = analysis/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/relative, target)
    paths = json.loads((OUT/'final_m5_source_sha256.json').read_text())
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == h for p, h in paths.items())
    (OUT/'current_m5_source_sha256.json').write_text(json.dumps(paths, indent=2)+'\n')
    print('120 development results re-audited; report saved; final sources unchanged')


if __name__ == '__main__':
    main()
