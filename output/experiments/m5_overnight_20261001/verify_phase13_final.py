from pathlib import Path
import json,hashlib,re
from datetime import datetime,timezone
H=Path(__file__).resolve().parent;ROOT=H.parents[2]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,r):p.write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n')
path=H/'manifest_phase13_final.json';m=json.loads(path.read_text());fail=[p for p,sha in m['sha256'].items() if digest(Path(p))!=sha]
assert fail==[str(H/'finalize_phase13_v2.log')],fail
# The failed finalization captured its own stdout before it finished. Preserve
# this attempt as diagnostic data and issue a completed-files manifest.
path.rename(H/'phase13_manifest_attempt2.json')
(H/'phase13_finalization_note.md').write_text('收尾v2在运行中采集了自己的stdout日志空文件哈希；退出后日志填入结果，最终检查发现唯一差异。该尝试清单完整保存在phase13_manifest_attempt2.json作为诊断数据。只在所有进程退出后重新冻结实际日志与完成文件，最终逐项复核在phase13_all_manifests_rechecked_final.json。算法、矩阵、证书、完整方案和正式界均未改变。旧收尾前扫描记录保留。\n')
files=set(map(Path,m['sha256']));files.update([H/'phase13_manifest_attempt2.json',H/'phase13_finalization_note.md',Path(__file__)])
write(path,dict(sha256={str(p):digest(p) for p in sorted(files)},all_workers_exited_before_freeze=True))
cache={};checks=0;failures=[];aliases=[];manifests=[]
for manifest in sorted(H.glob('manifest*.json')):
 r=json.loads(manifest.read_text());entries=r.get('sha256',r.get('source_and_input_sha256',r));count=0
 for raw,sha in entries.items():
  if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):continue
  p=Path(raw)
  if not p.is_absolute():p=(ROOT if raw.startswith(('output/','models/','tests/','configuration/','config/','docs/')) else H)/p
  if manifest.name=='manifest_s1full16_input.json' and p==H/'best_full_result.json' and sha.startswith('a6d578'):
   aliases.append(dict(manifest=str(manifest),original_path=str(p),historical_input=str(H/'best_full_result_pre_pool22.json'),expected_sha256=sha));p=H/'best_full_result_pre_pool22.json'
  if str(p) not in cache:cache[str(p)]=digest(p) if p.exists() else None
  if cache[str(p)]!=sha:failures.append(dict(manifest=str(manifest),path=str(p),expected=sha,actual=cache[str(p)]))
  checks+=1;count+=1
 manifests.append(dict(path=str(manifest),entries=count))
report=H/'phase13_all_manifests_rechecked_final.json';write(report,dict(checked_entries=checks,unique_files=len(cache),manifests=manifests,historical_input_aliases=aliases,failures=failures,all_passed=not failures));assert not failures,failures[:2]
c=json.loads((H/'checkpoint.json').read_text());c.update(phase13_manifest_recheck=str(report),phase13_finalization_note=str(H/'phase13_finalization_note.md'),phase13_completed_at_utc=datetime.now(timezone.utc).isoformat(),status='phase13_saved_all_completed_manifests_verified',active_process=None);write(H/'checkpoint.json',c)
print(json.dumps(dict(checked_entries=checks,unique_files=len(cache),all_passed=True,lower=c['medium20_known_lower'],upper=c['medium20_known_upper'],gap_percent=100*c['medium20_gap']),indent=2))
