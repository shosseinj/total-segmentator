import time
from sqlalchemy import select
from app.db.models import Outbox

def dispatch(repository,send):
    count=0
    with repository.session.begin() as db:
        rows=db.scalars(select(Outbox).where(Outbox.dispatch_status=='pending',Outbox.available_at<=time.time()).with_for_update(skip_locked=True).limit(100)).all()
        for row in rows:
            row.last_attempt_at=time.time()
            try:send(row.event_type,row.target_id)
            except Exception:continue
            row.dispatch_status='sent';count+=1
    return count
