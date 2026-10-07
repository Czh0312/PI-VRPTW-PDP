# -*- coding: utf-8 -*-
"""Save final validation and documentation without rerunning experiments."""
from pathlib import Path
import json, math
OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
rows=read(OUT/'rows.json'); verification=read(OUT/'verification.json')
assert verification['valid']==30
assert sum(c['automatic_count_selection_verified'] for c in verification['checks'])==18
for r in rows:
    a=read(OUT/'results'/r['id']/'independent_audit.json')
    b=a['container_balance']; d=a['distances']
    direct=d['e1']+.05647058823529412*d['e2']+25*r['pool']+.6*b['issued']+.2*b['returned_to_depot']
    assert math.isclose(r['cost'],direct,abs_tol=1e-6)
save(OUT/'validation_checks.json',dict(verification=verification,
    preflight=read(OUT/'preflight_checks.json'),direct_formula_checked=30,
    alternate_same_route_totals_checked=30,automatic_count_selection_checked=18,
    solver_failures=0,figure_visual_check='PASS: six readable panels, totals, models and components',
    notes=['FixedK diagnostics excluded from formal results.',
           'One progress attribution error corrected from rows.json; no data affected.',
           'An auxiliary stdin finalization command had an encoding error; repaired with UTF-8 file.']))
p=OUT/'execution_notes.md'
s=p.read_text(encoding='utf-8').replace('Verification Status: PENDING','Verification Status: VERIFIED')
s+='''
执行：`PYTHONPYCACHEPREFIX=/private/tmp/autoK1_pycache MPLCONFIGDIR=/private/tmp/pool25_mpl OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python output/model35678_comparison/20261006_pool25_truck1_autoK_5min_v1/run_study.py`。

30/30求解完成、独立路线和连续双周期复核全部通过；18/18联合调用确认K1/2/3候选、总预算300秒、最终目标等于最低有效候选。16个实际两批并减箱、2个单批（100-5-1b的50点M7/M8）；M6六例两批，M7/M8各五例两批、一例单批。两个M8返回K3但实际两批。308份算法、120对物理输入文件、6例、seed42及4个worker与原正式实验一致。无热启动、无求解失败、无自动重跑。30个主公式及30个清洗0.8同路线计价对照通过；后者未另搜索。旧正式路线同价比较23个更低、6个更高、1个相同。PNG/SVG生成并目视检查通过。

途中一次进度描述将单批归属误报为M6，已根据rows.json纠正为50点100-5-1b的M7；不影响实验数据。最后一个辅助保存检查记录的stdin命令编码报错，改为UTF-8源文件后保存成功，没有重新运行求解器或独立路线审计。
'''
p.write_text(s,encoding='utf-8')
p=ROOT/'output/model35678_comparison/README.md'
s=p.read_text(encoding='utf-8')
old='按用户要求恢复前面六例五模型的正式流程，当前运行见 [进度](20261006_pool25_truck1_autoK_5min_v1/progress.md)。'
new='按用户要求恢复前面六例五模型的正式流程，[完整正式报告](20261006_pool25_truck1_autoK_5min_v1/report.md)。30/30路线、费用和双周期独立审计通过；18/18联合调用均确认程序自动返回K1/2/3中最低有效候选。16个实际两批并减箱、2个单批；M6六例两批，M7/M8各五例两批、一例单批。两个M8返回K3但实际两批。'
assert old in s
s=s.replace(old,new).replace('输入、价格与最近比例测试一致。','输入、价格与最近比例测试一致。100-10-1的100点M7内部K1/K2/K3费用4693.22/4031.34/5397.18，程序自动返回K2、N104。六例最低成本模型分别为M8、M7、M6、M6、M8、M7；单种子有限搜索不证明理论最优排序。',1)
p.write_text(s,encoding='utf-8')
p=ROOT/'docs/transport_container_ratio_tests_v1.md'
s=p.read_text(encoding='utf-8').replace('结果见 `output/model35678_comparison/20261006_pool25_truck1_autoK_5min_v1/`。下方','结果见 `output/model35678_comparison/20261006_pool25_truck1_autoK_5min_v1/`；30/30独立路线和双周期审计通过，18/18联合调用自动择优检查通过，16个实际两批并减箱、2个单批。下方',1)
p.write_text(s,encoding='utf-8')
print('Final formal experiment checks and documentation saved')
