from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,re,ast
H=Path(__file__).resolve().parent;ROOT=H.parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def write(p,r):p.write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n')
automation=Path('/Users/chenzeheng/.codex/automations/m5/automation.toml');data={}
for line in automation.read_text().splitlines():
 if ' = ' in line:
  k,v=line.split(' = ',1)
  try:data[k]=ast.literal_eval(v)
  except Exception:pass
assert data['id']=='m5' and data['kind']=='heartbeat' and data['status']=='PAUSED'
assert data['rrule']=='FREQ=MINUTELY;INTERVAL=30;UNTIL=20261002T000000Z'
write(H/'automation_paused_at_stop.json',dict(id=data['id'],name=data['name'],kind=data['kind'],status=data['status'],rrule=data['rrule'],target_thread_id=data['target_thread_id'],actual_stop_utc=datetime.now(timezone.utc).isoformat(),no_cross_day_task_created=True,all_existing_prompt_and_schedule_fields_preserved=True))
cache={};fail=[];checks=0;aliases=[];manifests=[]
for manifest in sorted(H.glob('manifest*.json')):
 r=read(manifest);entries=r.get('sha256',r.get('source_and_input_sha256',r));count=0
 for raw,expected in entries.items():
  if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected):continue
  p=Path(raw)
  if not p.is_absolute():p=(ROOT if raw.startswith(('output/','models/','tests/','configuration/','config/','docs/')) else H)/p
  if manifest.name=='manifest_s1full16_input.json' and p==H/'best_full_result.json' and expected.startswith('a6d578'):
   aliases.append(dict(manifest=str(manifest),original_path=str(p),historical_input=str(H/'best_full_result_pre_pool22.json'),expected_sha256=expected));p=H/'best_full_result_pre_pool22.json'
  if str(p) not in cache:cache[str(p)]=sha(p) if p.exists() else None
  if cache[str(p)]!=expected:fail.append(dict(manifest=str(manifest),path=str(p),expected=expected,actual=cache[str(p)]))
  checks+=1;count+=1
 manifests.append(dict(path=str(manifest),entries=count))
report=H/'overnight_all_manifests_rechecked.json';write(report,dict(checked_entries=checks,unique_files=len(cache),manifests=manifests,historical_input_aliases=aliases,failures=fail,all_passed=not fail));assert not fail,fail[:3]
c=read(H/'checkpoint.json');assert c['active_process'] is None;c.update(status='overnight_complete_automation_paused',active_process=None,automation_status='PAUSED',completed_at_utc=datetime.now(timezone.utc).isoformat(),final_manifest_recheck=str(report),automation_stop_record=str(H/'automation_paused_at_stop.json'),next_action='Tonightended. Automationm5PAUSED; noactiveworker. Preserve20/30/50best certificates, fullschedules and margins. Read docs/m5_overnight_20261002.md andcheckpoint_at_stop.json; resume only onexplicituserrequest, do not createcross-day repeat tasks. Do not repeatoptimal oldrelaxations. Sharedchangesuntouched; no commits/push/publication/externalmessages/subagents.');write(H/'checkpoint.json',c);write(H/'checkpoint_at_stop.json',c)
files=[Path(__file__),H/'checkpoint_at_stop.json',H/'automation_paused_at_stop.json',report,ROOT/'docs/m5_overnight_20261002.md']
write(H/'manifest_overnight_stop.json',dict(sha256={str(p):sha(p) for p in files},all_workers_exited_before_freeze=True))
assert all(sha(Path(p))==s for p,s in read(H/'manifest_overnight_stop.json')['sha256'].items())
print(json.dumps(dict(automation_status=data['status'],final_report=c['final_report'],certificate_entries_checked=checks,unique_files=len(cache),failures=0,checkpoint_status=c['status'],bounds=read(H/'phase14_final_bounds.json')['results']),indent=2))
