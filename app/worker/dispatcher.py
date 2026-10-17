import time
from app.worker.celery_app import celery,settings
from app.db.repository import Repository
from app.storage.minio_store import MinioStore
from app.jobs.outbox import dispatch
from app.jobs.recovery import recover_stale_jobs,redispatch_unstarted
from app.worker.tasks import termination_confirmed
from app.jobs.retention import prune


def main():
    repo=Repository(settings.database_url);store=MinioStore(settings);recovered_at=0;pruned_at=time.monotonic()
    while True:
        try:
            dispatch(repo,lambda kind,id:celery.send_task(kind,args=[id],queue='inventory' if kind=='inventory' else 'gpu'))
            if time.monotonic()-recovered_at>=60:
                recover_stale_jobs(repo,store,lambda job:termination_confirmed(settings,job))
                redispatch_unstarted(repo,settings=settings)
                recovered_at=time.monotonic()
            if time.monotonic()-pruned_at>=3600:
                prune(repo,store,settings);pruned_at=time.monotonic()
        except Exception:
            # Never print upstream errors, connection strings or source metadata.
            print('stage=dispatcher error=DEPENDENCY_UNAVAILABLE',flush=True)
        time.sleep(5)

if __name__=='__main__':main()
