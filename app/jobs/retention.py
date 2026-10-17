"""Prune owned scratch and terminal orphan candidates; never source or published data."""
import fcntl,re,shutil,time
from sqlalchemy import select
from app.db.models import Attempt,Job,Scan
from app.jobs.leases import ACTIVE

def prune(repository,store,settings,now=None):
    now=time.time() if now is None else now
    summary={'orphans':0,'scratch':0}
    with repository.session.begin() as db:
        attempts=db.scalars(select(Attempt).where(Attempt.ended_at<now-7*86400).with_for_update(skip_locked=True)).all()
        published={p['key'] for job in db.scalars(select(Job)).all() for p in job.artifacts.values()}
        for attempt in attempts:
            job=db.get(Job,attempt.job_id,with_for_update=True)
            candidate=attempt.artifact_candidate
            if not job or job.attempt_token==attempt.token or candidate.get('pruned_at'):continue
            prefix=f'segmentation/{job.tenant_id}/{job.id}/attempts/{attempt.id}/'
            keys=[p['key'] for p in candidate.get('artifacts',{}).values()]
            if not keys or any(k in published or not k.startswith(prefix) for k in keys):continue
            for key in keys:
                # Missing objects are harmless; there is no bucket listing or input write.
                if store.verify_output(key,next(p['size'] for p in candidate['artifacts'].values() if p['key']==key),next(p['sha256'] for p in candidate['artifacts'].values() if p['key']==key)):
                    summary['orphans']+=1
                store.delete_output(key)
            attempt.artifact_candidate={**candidate,'pruned_at':now}
    for category in ('inventory','jobs'):
        for work in (settings.scratch/category).glob('*/*'):
            if not work.is_dir() or work.is_symlink() or not all(re.fullmatch('[a-f0-9]{32}',part) for part in work.parts[-2:]) or work.stat().st_mtime>now-86400:continue
            parent_id,token=work.parts[-2:]
            directory=settings.scratch/('inventory-locks' if category=='inventory' else '')
            directory.mkdir(parents=True,exist_ok=True)
            lock_path=directory/(parent_id+'.lock' if category=='inventory' else 'gpu0.lock')
            with lock_path.open('a') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:continue
                try:
                    with repository.session() as db:
                        job=db.get(Job,parent_id) if category=='jobs' else None
                        if job and job.status in ACTIVE and job.attempt_token==token:continue
                    shutil.rmtree(work);summary['scratch']+=1
                finally:fcntl.flock(lock,fcntl.LOCK_UN)
    return summary
