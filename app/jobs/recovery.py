"""Recover only previously approved work; never scan or approve new input."""
import time
from sqlalchemy import select
from app.db.models import Job,Attempt,Outbox,BatchJob,AuditEvent,Scan
from app.jobs.leases import ACTIVE

TRANSIENT={'STORAGE_UNAVAILABLE','DB_UNAVAILABLE','DISK_FULL','INVENTORY_IO_FAILED'}
DELAYS=[30,120,300]

def fail_attempt(repository,lease,code):
    with repository.session.begin() as db:
        job=db.get(Job,lease.job_id,with_for_update=True)
        if not job or job.attempt_token!=lease.token or job.worker_id!=lease.worker_id or job.status not in ACTIVE:return False
        attempt=db.scalar(select(Attempt).where(Attempt.token==lease.token));attempt.ended_at=time.time();attempt.outcome=code
        job.attempt_token=None;job.lease_until=None;job.safe_error=code
        if job.cancel_requested:
            job.status='cancelled';job.stage='cancelled';job.retry_at=None
        elif code in TRANSIENT and job.retry_count<3:
            job.retry_at=time.time()+DELAYS[job.retry_count];job.retry_count+=1;job.status='queued';job.stage='queued'
            db.add(Outbox(target_id=job.id,event_type='inference',available_at=job.retry_at))
        else:job.status='failed';job.stage='failed';job.retry_at=None
        return True

def recover_stale_jobs(repository,store,termination_confirmed,now=None):
    now=time.time() if now is None else now
    summary={'adopted':0,'requeued':0,'awaiting_termination':0,'cancelled':0}
    with repository.session.begin() as db:
        jobs=db.scalars(select(Job).where(Job.status.in_(ACTIVE),Job.lease_until<now).with_for_update(skip_locked=True)).all()
        for job in jobs:
            # The GPU lock is inherited by the child. Parent crash cannot free it early.
            if not termination_confirmed(job):summary['awaiting_termination']+=1;continue
            attempt=db.scalar(select(Attempt).where(Attempt.token==job.attempt_token))
            active=db.scalar(select(BatchJob).where(BatchJob.job_id==job.id,BatchJob.membership_status=='active'))
            if job.cancel_requested or not active:
                job.status='cancelled';job.stage='cancelled';summary['cancelled']+=1
            else:
                candidate=attempt.artifact_candidate if attempt else {}
                pointers=candidate.get('artifacts',{})
                try:valid=set(pointers)=={'seg','masks','measurements','manifest'} and all(store.verify_output(p['key'],p['size'],p['sha256']) for p in pointers.values())
                except Exception:continue  # I/O outage is not evidence that an artifact is missing.
                if valid:
                    job.artifacts=pointers;job.result=candidate['result'];job.status='succeeded';job.stage='succeeded';summary['adopted']+=1
                else:
                    job.status='queued';job.stage='queued';job.retry_at=now+30
                    db.add(Outbox(target_id=job.id,event_type='inference',available_at=job.retry_at));summary['requeued']+=1
            job.attempt_token=None;job.lease_until=None
            if attempt:attempt.ended_at=now;attempt.outcome='recovered_'+job.status
    return summary

def redispatch_unstarted(repository,now=None,settings=None):
    now=time.time() if now is None else now
    count=0
    with repository.session.begin() as db:
        rows=db.scalars(select(Outbox).where(Outbox.dispatch_status=='sent',Outbox.last_attempt_at<now-180).with_for_update(skip_locked=True)).all()
        for row in rows:
            target=db.get(Job if row.event_type=='inference' else Scan,row.target_id)
            restart_inventory=False
            if settings and row.event_type=='inventory' and target and target.status=='scanning':
                import fcntl
                directory=settings.scratch/'inventory-locks';directory.mkdir(parents=True,exist_ok=True)
                with (directory/(target.id+'.lock')).open('a') as lock:
                    try:
                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        restart_inventory=True
                    except BlockingIOError:pass
            if target and (target.status=='queued' or restart_inventory):row.dispatch_status='pending';count+=1
    return count
