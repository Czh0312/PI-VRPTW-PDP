"""Freeze explicit later-stage designs using the audited pilot attempt format."""
from copy import deepcopy
from importlib import metadata
from pathlib import Path
import os
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import zipfile

from experiments.runs import run_thesis_completion as driver


def prepare_design(args):
    from experiments.runs.run_model6_comparison import freeze_case
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance

    root = driver.ROOT
    design_path = Path(args.design).resolve()
    design = driver.read(design_path)
    out = Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite an existing study')
    if not design.get('jobs') or not design.get('sources') or not design.get('phase'):
        raise ValueError('Explicit design requires phase, sources and nonempty jobs')
    workers = design.get('workers', 2)
    overhead = design.get('timeout_overhead', 30)
    if not isinstance(workers, int) or workers < 1 or overhead <= 0:
        raise ValueError('Invalid worker count or outer grace')
    if not design.get('overhead_basis') or not design.get('selection_rule'):
        raise ValueError('Register overhead evidence and case selection before running')
    identifiers = [j['id'] for j in design['jobs']]
    if len(set(identifiers)) != len(identifiers) or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', x) for x in identifiers):
        raise ValueError('Job IDs must be unique safe directory names')
    for job in design['jobs']:
        if job['version'] not in design['sources'] or job['method'] not in ('alns', 'exact'):
            raise ValueError('Unknown source version or method')
        if job['budget'] <= 0 or job['model'] not in (2, 6, 7, 8):
            raise ValueError('Invalid model or call budget')
    for directory in ('inputs', 'cases', 'runs', 'sources', 'tables'):
        (out/directory).mkdir(parents=True, exist_ok=True)
    hashes, archives = {}, {}
    for version, source_spec in design['sources'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', version):
            raise ValueError('Unsafe source version')
        source = (root/source_spec['path']).resolve()
        source_hashes = driver.snapshot(source, out/'sources'/version)
        if not source_hashes:
            raise ValueError('Empty source snapshot')
        hashes.update({f'sources/{version}/{name}': digest for name, digest in source_hashes.items()})
        archive_path = out/f'source_{version}.zip'
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in source_hashes:
                relative = f'sources/{version}/{name}'
                entry = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(entry, (out/relative).read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        archives[archive_path.name] = driver.sha(archive_path)
    shutil.copyfile(Path(driver.__file__), out/'driver.py')
    hashes['driver.py'] = driver.sha(out/'driver.py')
    checks, jobs, evidence, configs = [], [], {}, {}
    for spec in design['jobs']:
        job = deepcopy(spec)
        source_case = str((root/job['case_path']).resolve())
        if source_case not in configs:
            key = f'case_{len(configs):04d}'
            config, sources = freeze_case(source_case, out, key)
            evidence.update(sources)
            target = out/'cases'/f'{key}.json'
            driver.write(target, config)
            problem = load_instance(*load_routing_case(target, use_pool_config=False))
            check = dict(case_path=target.relative_to(out).as_posix(), model=problem.model_number,
                         counts=list(getattr(problem, 'batch_count_candidates', [len(problem.operating_policy.batches)])),
                         orders=len(problem.orders), demand=problem.total,
                         physical_clock=problem.operating_policy.time_tick_seconds,
                         loading_mode=getattr(problem, 'loading_mode', 'batch_start'),
                         fixed_assignment=problem.options.get('fixed_assignment') if hasattr(problem, 'options') else None,
                         pool_min=getattr(problem, 'pool_min', None), pool_max=getattr(problem, 'pool_max', None))
            checks.append(check)
            configs[source_case] = check
        check = configs[source_case]
        if check['model'] != job['model']:
            raise ValueError('Job model differs from its frozen case')
        job.update(case_path=check['case_path'], counts=check['counts'], iterations=job.get('iterations', 10**9),
                   structure=job.get('structure', 'registered'), split=job.get('split', 'formal'),
                   family=job.get('family', job['case']), seed=job.get('seed', 42))
        job['command'] = [sys.executable, str(out/'driver.py'), 'worker', '--output', str(out),
                          '--job', job['id'], '--attempt', '{attempt}']
        jobs.append(job)
    random.Random(design.get('ordering_seed', 20261012)).shuffle(jobs)
    inputs = {p.relative_to(out).as_posix(): driver.sha(p) for folder in ('inputs', 'cases')
              for p in sorted((out/folder).iterdir()) if p.is_file()}
    environment = dict(captured_at=driver.now(), interpreter=sys.executable, python=sys.version,
        platform=platform.platform(), machine=platform.machine(), hostname=socket.gethostname(),
        logical_cpus=os.cpu_count(), workers=workers, exact_threads=1,
        dependencies={name: metadata.version(name) for name in ('numpy', 'scipy', 'pandas', 'pytest')})
    import gurobipy as gp
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag', 0)
        env.start()
        with gp.Model(env=env) as model:
            model.Params.Threads = 1
            x = model.addVar(vtype=gp.GRB.INTEGER, lb=0)
            model.addConstr(2*x >= 1)
            model.setObjective(x)
            model.optimize()
            if model.Status != gp.GRB.OPTIMAL or abs(model.ObjVal-1) > 1e-8:
                raise RuntimeError('Integer preflight failed')
            environment.update(gurobi_version=list(gp.gurobi.version()),
                               integer_preflight=dict(status=int(model.Status), objective=model.ObjVal))
    driver.write(out/'environment.json', environment)
    driver.write(out/'input_checks.json', checks)
    driver.write(out/'source_input_evidence.json', evidence)
    driver.write(out/'design.json', design)
    inputs['design.json'] = driver.sha(out/'design.json')
    manifest = dict(schema_version=2, phase=design['phase'], registered_at=driver.now(), original_root=str(out),
        jobs=jobs, workers=workers, timeout_overhead=overhead, timeout_overhead_basis=design['overhead_basis'],
        source_hashes=hashes, input_hashes=inputs, source_archive_hashes=archives,
        source_versions=list(design['sources']), source_provenance=design['sources'],
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        uncommitted_source_status=subprocess.check_output(['git', 'status', '--porcelain', '--',
            'models', 'experiments', 'tools', 'cli'], cwd=root, text=True, encoding='utf-8'),
        design_sha256=driver.sha(design_path), plan_count=len(jobs), total_call_budget=sum(j['budget'] for j in jobs),
        interpreter=sys.executable, ordering_seed=design.get('ordering_seed', 20261012), first_attempt_is_primary=True,
        prerequisites=[str((root/p).resolve()) for p in design.get('prerequisites', [])],
        selection_rule=design['selection_rule'], limitations=design.get('limitations', []))
    driver.write(out/'manifest.json', manifest)
    driver.write(out/'manifest.sha256.json', dict(sha256=driver.sha(out/'manifest.json')))
    driver.summarize(out, manifest)
    print(f'Frozen {len(jobs)} calls / {manifest["total_call_budget"]/3600:g} budget hours: {out}')
