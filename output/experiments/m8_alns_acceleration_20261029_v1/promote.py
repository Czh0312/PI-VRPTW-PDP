"""Install the validated evaluator after checking the complete paired evidence."""
from pathlib import Path
from run import HERE, ROOT, read, save, sha
from analyze import verify_plan

plan = verify_plan('confirmatory', 'run.py')
verify_plan('pilot', 'pilot_driver.py')
rows = list((HERE / 'trials' / 'confirmatory').rglob('summary.json'))
assert len(rows) == len(plan['jobs']) == 62
assert all(read(path)['audit_valid'] and read(path)['two_cycle_valid'] for path in rows)
stats = read(HERE / 'statistics.json')
a, b = stats['baseline'], stats['candidate']
assert b['small_feasible'] >= a['small_feasible']
assert b['mean_small_gap_percent'] <= a['mean_small_gap_percent'] + .1
assert b['worst_small_gap_percent'] <= a['worst_small_gap_percent'] + 1.
changed = [name for name in plan['sources']['baseline']
           if plan['sources']['baseline'][name] != plan['sources']['candidate'][name]]
relative = str(Path('models/routing/model_8/alns/evaluator.py'))
assert changed == [relative], changed
target = ROOT / relative
before = sha(target)
assert before == plan['sources']['baseline'][relative], 'The active file changed during the experiment; merge it explicitly.'
released = read(HERE / 'released_source_hashes.json')
assert read(HERE / 'released_replay_validation.json')['total_equal_evaluations'] == 8366
candidate = HERE / 'sources' / 'released' / relative
assert all(sha(HERE / 'sources' / 'released' / name) == digest for name, digest in released.items())
target.write_bytes(candidate.read_bytes())
assert sha(target) == released[relative]

readme = ROOT / 'models/routing/model_8/alns/README.md'
payload = readme.read_text(encoding='utf-8')
paragraph = ('\n不超过9个订单的小实例中，候选评价先根据总仓清洗完成时间与各批装载量推算所需箱池，再在该箱池下运行完整物理、费用与两周期审计。'
             '正常可行候选只做一次完整物理审计；箱池推算或审计失败时回退原评价路径，保留错误码及罚分。'
             '最终输出仍独立复核，不读取参考最优值作为搜索输入。'
             '七单算例的多种子短预算对照支持启用此路径；中大例尚未显示稳定质量收益，超过9单保留原评价路径。\n')
assert paragraph.strip() not in payload
needle = '快速评分用于候选排序或筛选；完整评价和独立业务审计决定可行性与最终成本。'
assert needle in payload
readme.write_text(payload.replace(needle, needle+'\n'+paragraph, 1), encoding='utf-8')

save(HERE / 'promotion.json', dict(default_enabled=True, production_file=str(target),
    before_sha256=before, after_sha256=sha(target), candidate_source_unchanged=True,
    affected_algorithm='M8 ALNS candidate evaluation only', exact_solver_changed=False,
    default_fast_path_max_orders=9, quality_validation_orders=7,
    larger_instances_use_original_evaluation=True,
    published_version='Size-scoped release; original all-size candidate trial data remain unchanged.',
    business_rules_or_costs_changed=False, original_evaluator_preserved=str(HERE / 'sources/baseline' / relative),
    paired_trials=62, small_seeds=[11, 23, 37], selected_integration_validation='pending'))
print(dict(default_enabled=True, changed_file=str(target), candidate_sha256=sha(target)))
