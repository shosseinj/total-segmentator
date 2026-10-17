import time
from sqlalchemy import select
from app.db.models import Job,BatchJob,Attempt,new_id
from app.contracts import JobLease

ACTIVE={'validating','running','exporting','uploading'}

def claim_job(repository,job_id,worker_id,lease_seconds=180):
    with repository.session.begin() as db:
        job=db.get(Job,job_id,with_for_update=True)
        if not job or job.status!='queued' or job.cancel_requested or job.retry_at and job.retry_at>time.time():return None
        if not db.scalar(select(BatchJob).where(BatchJob.job_id==job_id,BatchJob.membership_status=='active')):return None
        token=new_id();job.attempt_token=token;job.worker_id=worker_id;job.lease_until=time.time()+lease_seconds;job.status='validating';job.stage='validating'
        db.add(Attempt(job_id=job.id,token=token,worker_id=worker_id))
        return JobLease(job.id,token,worker_id)

def owned(job,lease):return bool(job and job.attempt_token==lease.token and job.worker_id==lease.worker_id and job.lease_until and job.lease_until>time.time() and not job.cancel_requested and job.status in ACTIVE)

def heartbeat(repository,lease,lease_seconds=180):
    with repository.session.begin() as db:
        job=db.get(Job,lease.job_id,with_for_update=True)
        if not owned(job,lease):return False
        job.lease_until=time.time()+lease_seconds
        attempt=db.scalar(select(Attempt).where(Attempt.token==lease.token));attempt.heartbeat_at=time.time()
        return True

def transition(repository,lease,stage):
    allowed={'validating':{'running'},'running':{'exporting'},'exporting':{'uploading'}}
    with repository.session.begin() as db:
        job=db.get(Job,lease.job_id,with_for_update=True)
        if not owned(job,lease) or stage not in allowed.get(job.stage,set()):return False
        job.stage=stage;job.status=stage
        return True
