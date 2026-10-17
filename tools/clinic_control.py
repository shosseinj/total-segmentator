"""Explicitly run ON THE CLINIC HOST. Never invoked by package construction."""
import argparse,json,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from .common import atomic_write,write_connection,read_connection,sign_handoff,verify_handoff

def export_values(original):
    values={k:str(v) for k,v in original.items() if k.startswith('SEG_') and k not in {'SEG_API_URL','SEG_SCRATCH','SEG_MODEL_CACHE'}}
    for name,key,port in [('POSTGRES','SEG_DATABASE_URL',15432),('REDIS','SEG_REDIS_URL',16379),('MINIO','SEG_S3_ENDPOINT',19000)]:
        parsed=urlsplit(values[key])
        default={'POSTGRES':5432,'REDIS':6379,'MINIO':9000}[name]
        if parsed.hostname not in {'127.0.0.1','localhost','::1'}:raise ValueError('CLINIC_ENDPOINT_NOT_LOOPBACK')
        values['CLINIC_'+name+'_HOST']='127.0.0.1';values['CLINIC_'+name+'_PORT']=str(parsed.port or default)
        userinfo=parsed.netloc.rsplit('@',1)[0]+'@' if '@' in parsed.netloc else ''
        values[key]=urlunsplit((parsed.scheme,userinfo+'127.0.0.1:'+str(port),parsed.path,parsed.query,parsed.fragment))
    values.update(SEG_SCRATCH='/work',SEG_MODEL_CACHE='/models',SEG_MR_ENABLED='false')
    return values

def no_active_jobs(repository):
    from sqlalchemy import select
    from app.db.models import Job
    from app.jobs.leases import ACTIVE
    with repository.session() as db:
        if db.scalar(select(Job.id).where(Job.status.in_(ACTIVE)).limit(1)):raise ValueError('ACTIVE_JOBS: finish or cancel running segmentation first')

def compose(root):
    return ['docker','compose','--env-file',str(root/'local-backend/segmentation/compose.env'),'-f',str(root/'services/dicom-segmentation/compose.yaml')]

def restart_viewer(root,runner):
    runner([str(root/'start-dicom-viewer.sh'),'--stop'],check=True,capture_output=True,text=True)
    runner([str(root/'start-dicom-viewer.sh')],check=True,capture_output=True,text=True)

def reject_native_workers(root,proc_root=Path('/proc')):
    path=root/'local-backend/segmentation/processes.json'
    if not path.exists():return
    try:
        records=json.loads(path.read_text())
        for record in records.values():
            pid=record['pid'];marker=record['marker']
            if type(pid) is not int or pid<1 or not isinstance(marker,str) or not marker:raise ValueError('INVALID_PROCESS_STATE')
            proc=proc_root/str(pid)
            try:
                if (proc/'cwd').resolve()==(root/'services/dicom-segmentation').resolve() and marker.encode() in (proc/'cmdline').read_bytes():raise ValueError('NATIVE_WORKERS_RUNNING: stop the native segmentation launcher and wait for termination')
            except FileNotFoundError:pass
            except PermissionError:raise ValueError('NATIVE_WORKER_STATUS_UNKNOWN') from None
    except (KeyError,AttributeError,TypeError,json.JSONDecodeError):raise ValueError('INVALID_PROCESS_STATE') from None

def activate(root,values,repository,runner=subprocess.run,restart=True,proc_root=Path('/proc')):
    root=Path(root);state=root/'local-backend/segmentation';backup=state/'gpu-handoff-backup.json'
    if backup.exists():raise ValueError('HANDOFF_ALREADY_ACTIVE')
    reject_native_workers(root,proc_root)
    no_active_jobs(repository)
    runner(compose(root)+['stop','api','inventory','dispatcher','gpu-worker'],check=True,capture_output=True,text=True)
    response=runner(compose(root)+['ps','--status','running','--services'],check=True,capture_output=True,text=True)
    if response.stdout.strip():raise ValueError('LOCAL_WORKERS_STILL_RUNNING')
    reject_native_workers(root,proc_root)
    no_active_jobs(repository)
    path=state/'env.json';original=json.loads(path.read_text())
    atomic_write(backup,json.dumps({'previous_api_url':original.get('SEG_API_URL')}))
    original['SEG_API_URL']='http://127.0.0.1:8098';atomic_write(path,json.dumps(original,indent=2))
    atomic_write(state/'gpu-handoff.json',json.dumps(sign_handoff(values),indent=2))
    if restart:restart_viewer(root,runner)

def rollback(root,repository,runner=subprocess.run):
    import httpx
    from sqlalchemy import select
    from app.db.models import WorkerHealth
    reject_native_workers(root)
    no_active_jobs(repository)
    with repository.session() as db:
        if db.scalar(select(WorkerHealth.id).where(WorkerHealth.role.in_(['gpu','inventory']),WorkerHealth.heartbeat_at>time.time()-100).limit(1)):raise ValueError('REMOTE_WORKERS_STILL_FRESH: stop GPU deployment and wait 100 seconds')
    try:
        httpx.get('http://127.0.0.1:8098/health/live',timeout=3,trust_env=False)
    except httpx.HTTPError:pass
    else:raise ValueError('REMOTE_API_STILL_RUNNING')
    state=root/'local-backend/segmentation';backup=state/'gpu-handoff-backup.json'
    previous=json.loads(backup.read_text());path=state/'env.json';values=json.loads(path.read_text())
    if previous['previous_api_url'] is None:values.pop('SEG_API_URL',None)
    else:values['SEG_API_URL']=previous['previous_api_url']
    atomic_write(path,json.dumps(values,indent=2))
    runner(compose(root)+['up','-d','migrate','api','inventory','dispatcher'],check=True,capture_output=True,text=True)
    restart_viewer(root,runner)
    backup.unlink();(state/'gpu-handoff.json').unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser(description='Run only on the existing clinic host with its service virtualenv.')
    parser.add_argument('action',choices=['export','activate','rollback'])
    parser.add_argument('--root',required=True,type=Path)
    args=parser.parse_args();root=args.root.resolve();state=root/'local-backend/segmentation'
    try:
        original=json.loads((state/'env.json').read_text())
        if args.action=='export':
            values=export_values(original);write_connection(state/'gpu-connection.env',values)
            print('Private connection exported to local-backend/segmentation/gpu-connection.env; never put it in the public ZIP.');return
        from app.db.repository import Repository
        repo=Repository(original['SEG_DATABASE_URL'])
        if args.action=='activate':
            exported=read_connection(state/'gpu-connection.env')
            if exported!=export_values(original):raise ValueError('CONNECTION_EXPORT_STALE')
            source=(root/'dicom-viewer/start_local.py').read_text()
            if "env.setdefault('SEG_API_URL'" not in source:raise ValueError('CLINIC_VIEWER_UPDATE_REQUIRED: use the updated clinic source')
            activate(root,exported,repo)
            print('Local segmentation stopped; Viewer uses loopback 8098. Transfer gpu-handoff.json privately and start GPU deployment.')
        else:rollback(root,repo);print('Local CPU deployment restored.')
    except ValueError as exc:raise SystemExit(str(exc)) from None
    except Exception:raise SystemExit('CLINIC_ACTION_FAILED: no credential output; inspect service availability and Docker permissions') from None

if __name__=='__main__':main()
