"""Keep this stage's patch separate from concurrent edits; hash final artifacts."""
import difflib
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
patch = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add(name, before, after):
    patch.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile='a/'+name if before else '/dev/null', tofile='b/'+name))


for name in ('models/routing/solvers/exact/multibatch_route_cg.py',
             'models/routing/solvers/exact/multibatch_route_enumeration.py'):
    add(name, (HERE/'before_source'/name).read_text(), (HERE/'final_source_snapshot'/name).read_text())
for name in ('models/routing/solvers/exact/multibatch_route_integer_cover.py',
             'tests/routing/test_multibatch_integer_cover.py',
             'tools/audit/benchmark_multibatch_integer_cover.py', 'docs/m5_integer_cover_20261001.md'):
    add(name, '', (ROOT/name).read_text())
blocks = {
    'models/routing/variants/model_5/exact/README.md': "\n第十四步可在上述完整路线域证书中增加 `integer_cover_time_limit=15`，\n在原总预算内求解完整二元路线覆盖，取更强的整数松弛下界；默认仍为 0。\n只有完整枚举域可以进入整数证书，限时保留已验证的 LP 界。\n本组 50 顾客 gap 进一步降到约 2.31%，完整 M5 最优性标志仍为 False。\n见 [完整整数覆盖下界](../../../../../docs/m5_integer_cover_20261001.md)。\n",
    'tools/README.md': "\n`audit/benchmark_multibatch_integer_cover.py` 冻结相同输入、完整可行上界与源码，\n在原总预算内比较完整路线覆盖 `lp` 与 `integer`，另提供强制中断枚举的 `fallback`。\n`--integer-time 15` 是每仓整数阶段上限；完整二元模型和连续模型分别压缩导出。\n见 [完整整数覆盖与全矩阵复核](../docs/m5_integer_cover_20261001.md)。\n",
    'docs/exact_solver_architecture.md': "\nM5 完整二层路线域还可通过 `integer_cover_time_limit` 在原预算内求解二元覆盖，\n保留全部覆盖行和有限车队总趟数行，用完整整数松弛的求解器下界加强 LP 证书。\n部分枚举不进入整数证书，限时保留已有有效界；默认关闭。\n整数覆盖仍放松真实跨层日程，见 [完整整数覆盖下界](m5_integer_cover_20261001.md)。\n",
}
for name, block in blocks.items():
    after = (ROOT/name).read_text()
    assert after.count(block) == 1, name
    add(name, after.replace(block, ''), after)
(HERE/'changes.patch').write_text(''.join(patch))
value = json.loads((HERE/'validation.json').read_text())
live, drift = {}, {}
differences = []
for name, validated_hash in value['current_source_sha256'].items():
    live[name] = sha(ROOT/name)
    if live[name] != validated_hash:
        assert name in ('models/routing/solvers/exact/multibatch_route_cg.py',
                        'models/routing/solvers/exact/multibatch_route_pricing.py'), name
        drift[name] = dict(validated_sha256=validated_hash, live_sha256=live[name],
            reason='Concurrent default-off batch pricing and dual repair; complete enumeration branch and integer module unchanged.')
        differences.extend(difflib.unified_diff((HERE/'final_source_snapshot'/name).read_text().splitlines(True),
            (ROOT/name).read_text().splitlines(True), fromfile='validated/'+name, tofile='live/'+name))
(HERE/'concurrent_source.diff').write_text(''.join(differences))
for name in ('multibatch_route_enumeration.py', 'multibatch_route_integer_cover.py'):
    key = 'models/routing/solvers/exact/'+name
    assert live[key] == value['current_source_sha256'][key]
report = ROOT/'docs/m5_integer_cover_20261001.md'
for link in re.findall(r'\]\(([^)]+)\)', report.read_text()):
    if '://' not in link:
        assert (report.parent/link).exists(), link
test_line = (HERE/'post_concurrent_api.log').read_text().splitlines()[-1]
assert re.match(r'\d+ passed', test_line), test_line
value.update(report_sha256=sha(report), changes_patch_sha256=sha(HERE/'changes.patch'),
    live_source_sha256=live, source_drift_after_validation=drift,
    concurrent_source_diff_sha256=sha(HERE/'concurrent_source.diff'),
    post_concurrent_api_test_log=test_line, post_concurrent_api_log_sha256=sha(HERE/'post_concurrent_api.log'),
    finalizer_sha256=sha(Path(__file__)))
(HERE/'validation.json').write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print('Source integrity and report links verified; targeted patch saved.')
print(json.dumps(dict(formal_runs=value['formal_runs'], lp_replays=len(value['complete_lp_checks']),
    integer_replays=len(value['integer_cover_checks']), e1_replays=len(value['e1_checks']),
    regression=value['test_log'], live_api=test_line), ensure_ascii=False))
