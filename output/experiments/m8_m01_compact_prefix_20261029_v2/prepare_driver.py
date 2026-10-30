"""Create a second frozen M01 experiment with certified stock frontier cuts."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT/'output/experiments/m8_m01_compact_prefix_20261029_v1'


if __name__ == '__main__':
    target = HERE/'study.py'
    if target.exists():
        raise FileExistsError(target)
    source = (OLD/'study.py').read_text(encoding='utf-8')
    source = source.replace('from experiments.runs.model8_compact_prefix import transport_certificate, build_prefix, extract_prefix, supply_start',
        'from experiments.runs.model8_compact_prefix import transport_certificate, build_prefix, extract_prefix, supply_start\n'
        'from experiments.runs.model8_frontier import stock_frontier, normalize_vehicle_ids, add_frontier_cuts')
    source = source.replace("files = [CASE,WITNESS,Path(__file__),ROOT/'experiments/runs/model8_compact_prefix.py']",
        "files = [CASE,WITNESS,Path(__file__),ROOT/'experiments/runs/model8_compact_prefix.py', ROOT/'experiments/runs/model8_frontier.py', ROOT/'experiments/runs/model8_stock_relaxation.py']")
    source = source.replace("    save('transport_certificate.json',certificate)\n    assert certificate['retained_K'] == [2,3,4,5]", """    save('transport_certificate_initial.json',certificate)
    frontier = stock_frontier(p,certificate,deadline=min(begun+120,deadline-300),per_pair_seconds=3,
        on_progress=lambda value: save('progress.json',dict(elapsed=time.perf_counter()-begun,**value)))
    save('stock_frontier.json',frontier)
    certificate['initial_retained_K'] = certificate['retained_K']
    certificate['retained_K'] = frontier['retained_K']
    certificate['stock_global_lower_bound'] = frontier['global_lower_bound']
    save('transport_certificate.json',certificate)
    assert (best['cycle_batch_count'],best['container_pool_size']) in {(r['K'],r['N']) for r in frontier['retained_pairs']}""")
    source = source.replace("m,ctx = build_prefix(p,certificate['retained_K'],certificate=certificate,deadline=deadline)",
        "m,ctx = build_prefix(p,certificate['retained_K'],certificate=certificate,deadline=deadline,vehicle_symmetry=True)\n"
        "    strengthening = add_frontier_cuts(p,m,ctx,frontier,certificate)\n"
        "    save('strengthening.json',strengthening)")
    source = source.replace("        start_info = supply_start(p,m,ctx,best)",
        "        best = normalize_vehicle_ids(p,best)\n        save('normalized_initial_result.json',best)\n        start_info = supply_start(p,m,ctx,best)")
    source = source.replace("        m.Params.MIPGapAbs = 0\n", """        m.Params.MIPGapAbs = 0
        m.Params.Method = 2
        m.Params.DegenMoves = 0
        m.Params.CutPasses = 3
        m.Params.Cuts = 1
        m.Params.MIPFocus = 1
        m.Params.Heuristics = .2
        save('solver_parameters.json',dict(Threads=1,Seed=42,MIPGap=0,MIPGapAbs=0,Method=2,
            DegenMoves=0,CutPasses=3,Cuts=1,MIPFocus=1,Heuristics=.2))
""")
    source = source.replace("fallback = min(certificate['per_K_lower_bound'].values())", "fallback = max(min(certificate['per_K_lower_bound'].values()),frontier['global_lower_bound'])")
    source = source.replace("certificate_seconds=certificate_seconds,build_seconds=build_seconds,", "certificate_seconds=certificate_seconds,frontier_seconds=frontier['seconds'],build_seconds=build_seconds,")
    target.write_text(source,encoding='utf-8')
    print(str(target))
