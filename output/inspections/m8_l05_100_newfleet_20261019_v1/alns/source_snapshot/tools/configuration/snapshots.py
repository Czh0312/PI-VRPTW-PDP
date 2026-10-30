"""Freeze composed configuration as self-contained values plus source evidence."""
import hashlib
import json

from .loader import config_sources, load_json, resolve_project_path


def freeze_input(source, target, output_root):
    source = resolve_project_path(source).resolve()
    records = {}
    sources = config_sources(source)
    for dependency in sources:
        payload = dependency.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        destination = (target if dependency == source else
                       target.parent / f'parameter_{digest[:16]}_{dependency.name}')
        destination.write_bytes(payload)
        records[str(dependency)] = dict(sha256=digest, snapshot=str(destination.relative_to(output_root)))
    if len(sources) > 1:
        # Workers must not reread live parameters after the experiment starts.
        resolved = target.with_name(target.stem+'_resolved.json')
        resolved.write_text(json.dumps(load_json(source), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        records[str(source)].update(resolved_snapshot=str(resolved.relative_to(output_root)),
                                    resolved_sha256=hashlib.sha256(resolved.read_bytes()).hexdigest())
    return records
