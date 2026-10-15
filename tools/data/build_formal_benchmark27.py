"""Build the 27 matched base cases for all eight model entry points.

No routing solver is called. Every runtime input is copied inside the new
dataset; the old selection and source datasets remain provenance only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import csv
import hashlib
import json
from pathlib import Path

from tools.configuration.loader import PROJECT_ROOT, load_json, resolve_project_path, load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.policy import MultiBatchPolicy
from tools.data.formal_benchmark27_bounds import productive_count_certificate


VERSION = 'formal_benchmark27_v1'
DEFAULT_SELECTION = 'config/experiments/benchmark27_v1/selection.json'
DEFAULT_OUTPUT = 'data/formal_benchmark27_v1'
MODES = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}
SIZES = ('small', 'medium', 'large')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reference(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def single_policy(source):
    """One initial issue; preserve the reference final cutoff and cycle end."""
    checked = MultiBatchPolicy.from_config(source)
    result = deepcopy(source)
    result.update(description='Single-batch projection; same physical cycle and final recovery cutoff.',
                  batches=[dict(id='b1', delivery_duration=checked.minutes(
                      checked.batches[-1].cutoff-checked.cycle_start),
                      return_duration=checked.minutes(checked.cycle_end-checked.batches[-1].cutoff))])
    MultiBatchPolicy.from_config(result)
    return result


def parameter_contract(model, record, orders, policy, joint_options):
    assignment = {o['customer_id']: o['satellite_id'] for o in orders}
    fixed = dict(packaging='single_use' if model < 3 else 'reusable',
                 cycle_start=policy['cycle_start'], cycle_end=policy['cycle_end'])
    optimized = ['routes', 'vehicle_assignment', 'service_times']
    inactive = []
    if model in (2, 4):
        optimized.append('customer_satellite_assignment')
    else:
        fixed['customer_satellite_assignment'] = assignment
    if model <= 5:
        fixed.update(batch_count=len(policy['batches']), calendar=policy['batches'],
                     order_batch_assignment={o['order_id']: o['delivery_batch_id'] for o in orders})
        if model >= 3:
            fixed['container_pool_size'] = record['total_container_uses']
        else:
            inactive = ['reusable_container_pool', 'pickup_tasks', 'cleaning', 'container_cycle_closure']
    else:
        fixed['schedule_mode'] = MODES[model]
        optimized += ['batch_count', 'order_batch_assignment', 'container_pool_size',
                      'common_cutoff' if model == 6 else 'independent_batch_cutoffs']
        if model == 8:
            optimized.append('batch_lengths')
        else:
            fixed['batch_length_rule'] = 'cycle_length / selected_batch_count'
    return dict(model=model, base_case_id=record['id'], source_family=record['family_id'],
                demand_container_uses=record['total_container_uses'],
                objective='distance' if model <= 5 else 'cost',
                fixed_parameters=fixed, optimized_decisions=optimized, inactive_components=inactive,
                decision_bounds=joint_options or {},
                reference_only=dict(customer_satellite_assignment=assignment,
                    container_pool_size=record['total_container_uses']),
                notes=['The model number activates packaging and free-assignment semantics in the solver.',
                       'For M6-M8, policy.container_pool_size is a reference; pool_min/pool_max define the decision.',
                       'For M2/M4, satellite_id is the initial/reference assignment and is not fixed.',
                       'Shared economic coefficients do not change the native distance objective of M1-M5.',
                       'M5 fixes dispatch slots but does not enforce positive recovery in each slot; M6-M8 do. '
                       'An ablation attributing differences only to released decisions must harmonize this rule.'])


def build_suite(output=DEFAULT_OUTPUT, selection=DEFAULT_SELECTION):
    out, source = resolve_project_path(output).resolve(), resolve_project_path(selection)
    design = read(source)
    records = design['cases']
    if len(records) != 27 or Counter(r['size'] for r in records) != dict.fromkeys(SIZES, 9):
        raise ValueError('Selection must contain exactly nine cases in each size')
    if len({r['id'] for r in records}) != 27:
        raise ValueError('Base case IDs must be unique')
    # Check all source bytes before creating anything, never silently refresh.
    for record in records:
        for key, path in record['common_reference_files'].items():
            if digest(resolve_project_path(path)) != record['reference_sha256'][key]:
                raise ValueError(f'Selected source changed: {record["id"]}/{key}')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty dataset: {out}')
    out.mkdir(parents=True, exist_ok=True)
    write_json(out/'selection.json', design)
    write_json(out/'comparison_protocol.json', design['review_protocol'])
    manifest = dict(dataset_version=VERSION, dataset_revision=2, base_case_count=27, model_config_count=216,
                    models=list(range(1, 9)), sizes=dict.fromkeys(SIZES, 9),
                    source_block_count=len({r['source_block'] for r in records}),
                    generator=reference(Path(__file__)), generator_sha256=digest(__file__),
                    selection_source=reference(source), selection_sha256=digest(source),
                    batch_definition='M6-M8: each counted batch has positive delivery and depot-return quantities',
                    runtime_path_basis='Project-root-relative paths; no runtime dependencies outside this dataset',
                    objective_by_model={str(i): 'distance' if i <= 5 else 'cost' for i in range(1, 9)},
                    cases=[], base_cases=[], limitations=[
                        '27 spatial networks are derived from 14 source blocks; nested sizes remain paired.',
                        'Synthetic appointments and economic coefficients, not measured operating costs.',
                        'Input validation does not certify route feasibility or optimality.',
                        'M6/M7 refine equal-partition clocks when necessary; M8 uses the original integer clock.'])
    for record in records:
        shared = out/'shared'/record['size']/record['id']
        shared.mkdir(parents=True)
        files = record['common_reference_files']
        common = {}
        for key, filename in [('data', 'network.csv'), ('routing_config', 'fleet.json'),
                              ('economic_config', 'economic.json')]:
            origin = resolve_project_path(files[key])
            target = shared/filename
            if key == 'data':
                target.write_bytes(origin.read_bytes())
            else:
                value = load_json(origin)
                if any(k in value for k in ('evidence_file', 'operating_policy')):
                    raise ValueError(f'Unexpected external dependency in {key}: {origin}')
                write_json(target, value)
            common[key] = reference(target)
        orders = read(resolve_project_path(files['orders']))
        policy = load_json(files['operating_policy'])
        calendar = design['reviewed_reference_calendar']
        length = Decimal(str(policy['cycle_end']-policy['cycle_start']))/calendar['batch_count']
        delivery = length*Decimal(str(calendar['cutoff_ratio']))
        policy['batches'] = [dict(id=f'b{i+1}',
            delivery_duration=float(delivery), return_duration=float(length-delivery))
            for i in range(calendar['batch_count'])]
        policy['description'] = 'Reviewed common reference calendar: three equal batches, 70% cutoff; see provenance.'
        MultiBatchPolicy.from_config(policy)
        if sum(o['container_count'] for o in orders) != record['total_container_uses']:
            raise ValueError(f'Demand changed: {record["id"]}')
        write_json(shared/'reference_orders.json', orders)
        write_json(shared/'reference_policy.json', policy)
        provenance = dict(base_case_id=record['id'], family_id=record['family_id'],
                          source_block=record['source_block'], customer_count=record['customer_count'],
                          total_container_uses=record['total_container_uses'], variant=record['variant'],
                          source_files=files, source_sha256=record['reference_sha256'],
                          reference_calendar_adaptation=calendar,
                          selection_rule=design['selection_rule'])
        write_json(shared/'provenance.json', provenance)
        common['dataset_metadata'] = reference(shared/'provenance.json')
        base = {k: record[k] for k in ('id', 'size', 'customer_count', 'family_id',
                                      'source_block', 'total_container_uses')}
        base['models'] = {}
        for model in range(1, 9):
            folder = out/f'model_{model}'/record['size']/record['id']
            model_orders = deepcopy(orders)
            model_policy = single_policy(policy) if model <= 4 else deepcopy(policy)
            if model <= 4:
                for order in model_orders:
                    order.update(delivery_batch_id='b1', allowed_batch_ids=['b1'])
            elif model == 5:
                for order in model_orders:
                    order['allowed_batch_ids'] = [order['delivery_batch_id']]
            if model == 6:
                fixed_cfg, fixed_fleet = load_routing_case(base['models']['5'])
                certificate = productive_count_certificate(load_instance(fixed_cfg, fixed_fleet))
                write_json(shared/'batch_count_certificate.json', certificate)
            options = (dict(schedule_mode=MODES[model],
                            batch_count_candidates=certificate['candidates_by_model'][str(model)], min_phase_minutes=1,
                            pool_min=1, pool_max=record['total_container_uses']) if model >= 6 else {})
            write_json(folder/'orders.json', model_orders)
            write_json(folder/'policy.json', model_policy)
            contract = parameter_contract(model, record, model_orders, model_policy, options)
            write_json(folder/'parameters.json', contract)
            cfg = dict(schema_version=2, case_name=f'{VERSION}_{record["id"]}_M{model}',
                       model=model, solver='alns', **common,
                       orders=reference(folder/'orders.json'), operating_policy=reference(folder/'policy.json'),
                       container_pool_size=record['total_container_uses'],
                       exact_backend='joint_batch_indexed' if model >= 6 else 'multibatch_indexed',
                       exact_time=300, alns_time=30, alns_iter=10**9, seed=42, save_plots=False,
                       dataset_version=VERSION, base_case_id=record['id'],
                       parameter_contract=reference(folder/'parameters.json'))
            if options:
                cfg['joint_batch_options'] = options
                cfg['batch_count_certificate'] = reference(shared/'batch_count_certificate.json')
            write_json(folder/'case.json', cfg)
            entry = dict(base_case_id=record['id'], model=model, size=record['size'],
                         customer_count=record['customer_count'], family_id=record['family_id'],
                         source_block=record['source_block'], total_container_uses=record['total_container_uses'],
                         case_path=reference(folder/'case.json'), parameters=reference(folder/'parameters.json'))
            manifest['cases'].append(entry)
            base['models'][str(model)] = entry['case_path']
        manifest['base_cases'].append(base)
    for model in range(1, 9):
        directory = out/f'model_{model}'
        profile = design['model_profiles'][str(model)]
        (directory/'README.md').write_text(
            f'# 模型 {model} 数据集\n\n小、中、大各9例，共27例。每例的 `case.json` 可直接运行，'
            '`parameters.json` 列出固定参数、优化决策及不适用的机制。\n\n'
            f'模型设置：`{json.dumps(profile, ensure_ascii=False)}`\n\n'
            '最终运行范围以各例 case.json 为准；M6–M8 从完整正整数域中仅排除已证明不可行的批次数，'
            '共同物理输入位于本数据集 shared 目录。\n', encoding='utf-8')
        for size in SIZES:
            lines = [f'# 模型 {model} / {size}', '', '| 算例 | 顾客数 | 需求箱次 Q | 配置 |',
                     '| --- | ---: | ---: | --- |']
            for rec in records:
                if rec['size'] == size:
                    lines.append(f'| {rec["id"]} | {rec["customer_count"]} | {rec["total_container_uses"]} | '
                                 f'[{rec["id"]}/case.json]({rec["id"]}/case.json) |')
            (directory/size/'README.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    with (out/'case_index.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest['cases'][0]))
        writer.writeheader()
        writer.writerows(manifest['cases'])
    dataset_ref = reference(out)
    readme = f'''# 正式主数据集：八模型 × 小中大各九例

共27个基础算例、216份可运行模型配置。每个模型有 small / medium / large 三个目录，每个目录九例。

```text
formal_benchmark27_v1/
├── model_1/ ... model_8/
│   ├── small/S01 ... S09/
│   ├── medium/M01 ... M09/
│   └── large/L01 ... L09/
│       ├── case.json        运行入口
│       ├── orders.json      相同物理订单，模型特定批次绑定
│       ├── policy.json      模型特定固定或参考日历
│       └── parameters.json  固定／优化／不适用参数说明
├── shared/                 27例的网络、车辆、费用及来源快照
├── manifest.json           八模型配对索引和输入哈希
├── case_index.csv          216项可读索引
└── selection.json          原始选例设计快照
```

| 规模 | 顾客数分布 | 每模型例数 |
| --- | --- | ---: |
| small | 7/10/12顾客各3例 | 9 |
| medium | 20顾客4例、30顾客5例 | 9 |
| large | 50顾客4例、100顾客5例 | 9 |

所有算例使用同一窗口／使用时间设计。每例跨模型保持需求、坐标、绝对送取窗、释放、使用与服务时间、车队和周期一致。批次绑定、日历与箱池是否固定由模型决定。M1/M2 不执行回收和清洗；M2/M4 的订单原分仓只是参考。

M1–M4固定单批，M3/M4固定N=Q。M5固定三批、等长日历、70%共同分界、参考订单分批及N=Q。M6优化K、共同分界、订单分批和N；M7改为独立分界；M8再放开批长。各实际批次必须有配送和回总仓箱量。M6–M8配置中的container_pool_size=Q是参考值，优化范围由pool_min/pool_max决定。

修订2将全部27例的共同参考分界从75%统一改为70%（每批182分钟配送阶段、78分钟回库阶段）；单批模型同步使用相同末次分界19:42。原M03/M5经精确求解判为不可行，根因涉及末批回库与清洗时间不足；原L09/M5也未找到可行解。修订保留需求、坐标、车队、费用、送取窗、周期终点和订单参考分批。修改不是最优分界标定，不根据模型的相对收益挑选案例。原始来源哈希和日历变更原因保存在各例provenance中。

修订2保留原始K=1…390的数学域，配置仅排除经证明不可能的K：每个非空配送批次至少需要一个订单；M6/M7的首批还必须足够长，容纳最早可能的实际回库。每例 shared/batch_count_certificate.json 给出逐订单乐观回库下界、公式、假设和保留范围。M6/M7保留3–5个候选，M8保留7–100个候选。这是可行域等价的预处理，不是人为限定K或按求解结果选数；不改变订单、车辆、费用或任何可行解。

M1–M5原生目标为距离，M6–M8为成本。共同费用文件供一致评价，不意味着改变了原生目标。费用为合成单位；单用包装经济评价需要相应的一次性包装价格，不能直接套用循环箱池费用。

27套空间网络来自14个原始来源块，中大规模含嵌套子集。来源和同源关系见manifest；种子重复不计为新算例。

在项目根目录运行：

```sh
.venv/bin/python main.py route --case {dataset_ref}/model_1/small/S01/case.json --time 30
.venv/bin/python main.py route --case {dataset_ref}/model_8/large/L09/case.json --time 30
.venv/bin/python -m tools.audit.audit_formal_benchmark27 --dataset {dataset_ref}
```

默认ALNS 30秒、精确300秒、seed=42是统一入口默认值；正式多种子实验另行冻结预算和环境。生成程序拒绝覆盖非空数据集。全部运行输入在此数据集内部，配置路径相对项目根目录。来源路径仅作追溯，不在求解时加载。

输入核验及必要条件检查不等于全部算例已找到可行解或证明最优；实际状态以独立运行和审计为准。审计报告位于 `output/inspections/formal_benchmark27_v1/`。

发布版的validation目录另保存216份可行调度及独立重放记录。生成器之后运行 `python -m tools.audit.certify_formal_benchmark27` 可建立这些见证，`--verify` 可复核；求解器不会自动把见证当初始解。完整审查记录见 `output/inspections/formal_benchmark27_review_20261015/README.md`，比较与费用口径见本目录comparison_protocol.json。
'''
    (out/'README.md').write_text(readme, encoding='utf-8')
    manifest['artifact_sha256'] = {p.relative_to(out).as_posix(): digest(p)
                                   for p in sorted(out.rglob('*')) if p.is_file()}
    write_json(out/'manifest.json', manifest)
    return out/'manifest.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--selection', default=DEFAULT_SELECTION)
    args = parser.parse_args()
    print(build_suite(args.output, args.selection))


if __name__ == '__main__':
    main()
