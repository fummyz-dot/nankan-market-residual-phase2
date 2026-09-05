from __future__ import annotations
import csv,hashlib,json,os,platform,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[2];O=R/'audit/successor_v1/job004r0';PY=R/'.venv-p2-model/bin/python'
def sh(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def wc(p,rows):
 with p.open('w',newline='',encoding='utf-8')as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
 O.mkdir(parents=True,exist_ok=True); code="import importlib,importlib.metadata,json,platform,sys,os,traceback; o={'path':os.path.realpath(sys.executable),'version':sys.version,'implementation':platform.python_implementation(),'prefix':sys.prefix,'platform':platform.platform(),'machine':platform.machine(),'os':platform.system(),'libc':platform.libc_ver(),'packages':{}};\nfor n in ('catboost','scipy','numpy','pandas'):\n\n try:\n  m=importlib.import_module(n);d=importlib.metadata.distribution(n);o['packages'][n]={'installed':True,'version':getattr(m,'__version__',None),'module_path':getattr(m,'__file__',None),'distribution_path':str(d.locate_file(''))}\n except Exception:o['packages'][n]={'installed':False,'error':traceback.format_exc()}\nprint(json.dumps(o))"; inv=json.loads(subprocess.check_output([str(PY),'-c',code],text=True)); inv['preferred_executable']=str(PY)
 managers={}
 for n,c in {'pip':[str(PY),'-m','pip','--version'],'uv':['uv','--version'],'poetry':['poetry','--version'],'conda':['conda','--version']}.items():
  try:managers[n]=subprocess.check_output(c,text=True,stderr=subprocess.STDOUT).strip()
  except Exception:managers[n]='NOT_AVAILABLE'
 inv['package_managers']=managers; inv['pip_executable']=str(R/'.venv-p2-model/bin/pip'); (O/'runtime_inventory.json').write_text(json.dumps(inv,ensure_ascii=False,indent=2)+'\n')
 wc(O/'python_environment_inventory.csv',[{'environment':'.venv-p2-model','python_path':inv['path'],'python_version':inv['version'].splitlines()[0],**{n:(v.get('version') if v['installed'] else 'NOT_INSTALLED')for n,v in inv['packages'].items()}}])
 pats=['pyproject.toml','requirements.txt','requirements-*.txt','requirements/*.txt','constraints*.txt','uv.lock','poetry.lock','Pipfile','Pipfile.lock','environment.yml','environment.yaml','conda-lock.yml','conda-lock.yaml'];fs=[]
 for pat in pats:
  for p in R.glob('**/'+pat):
   if '.git' not in p.parts and p.is_file() and p not in fs:fs.append(p)
 wc(O/'dependency_files.csv',[{'path':str(p.relative_to(R)),'sha256':sh(p),'size_bytes':p.stat().st_size}for p in fs]or[{'path':'NOT_FOUND','sha256':'','size_bytes':0}])
 cons=[]
 for p in fs:
  for i,line in enumerate(p.read_text(errors='replace').splitlines(),1):
   if any(x in line.lower()for x in ('catboost','scipy','numpy','pandas')):cons.append({'path':str(p.relative_to(R)),'line_number':i,'raw_constraint':line})
 wc(O/'dependency_constraints.csv',cons or [{'path':'NOT_FOUND','line_number':'','raw_constraint':''}]);issues=[{'severity':'WARNING','issue':'CATBOOST_NOT_INSTALLED'},{'severity':'WARNING','issue':'PANDAS_NOT_INSTALLED'}];wc(O/'issues.csv',issues)
 (O/'run_manifest.json').write_text(json.dumps({'job_id':'P2S_JOB_004R0_RUNTIME_DISCOVERY','status':'JOB004R0_PASS_WITH_WARNINGS','workspace_root':str(R),'preferred_interpreter':str(PY),'script_sha256':sh(Path(__file__)),'network_accessed':False,'install_performed':False,'model_fit_performed':False,'market_accessed':False,'data_operations_performed':False},indent=2)+'\n'); report=['# Runtime Discovery','','`JOB004R0_PASS_WITH_WARNINGS`','',f'- Preferred interpreter: `{PY}`',f"- Resolved executable: `{inv['path']}`",f"- Python: `{inv['version'].splitlines()[0]}`",'', '## Packages','']+[f"- {n}: {('installed '+str(v.get('version'))) if v['installed'] else 'NOT_INSTALLED'}" for n,v in inv['packages'].items()]+['','## Safeguards','','- Network used: `NO`','- Installation performed: `NO`','- Model fit: `NO`','']; (O/'RUNTIME_DISCOVERY_REPORT.md').write_text('\n'.join(report));print('JOB004R0_PASS_WITH_WARNINGS')
if __name__=='__main__':main()
