"""Persistent approval, inventory and fenced publication state."""
import time, uuid
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def new_id():return uuid.uuid4().hex

class Base(DeclarativeBase):pass

class Scan(Base):
    __tablename__='scans'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(String,index=True)
    source_id:Mapped[str]=mapped_column(String)
    requested_scope:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String,default='queued')
    created_by:Mapped[str]=mapped_column(String)
    counters:Mapped[dict]=mapped_column(JSON,default=dict)
    completed_at:Mapped[float|None]=mapped_column(Float)
    safe_error:Mapped[str|None]=mapped_column(String)

class ScanObject(Base):
    __tablename__='scan_objects'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    scan_id:Mapped[str]=mapped_column(ForeignKey('scans.id'),index=True)
    snapshot:Mapped[dict]=mapped_column(JSON)
    inventory_status:Mapped[str]=mapped_column(String,default='pending')
    safe_error:Mapped[str|None]=mapped_column(String)
    retry_count:Mapped[int]=mapped_column(Integer,default=0,server_default='0')
    retry_at:Mapped[float|None]=mapped_column(Float)

class Series(Base):
    __tablename__='series_items'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    scan_object_id:Mapped[str]=mapped_column(ForeignKey('scan_objects.id'),index=True)
    study_uid:Mapped[str]=mapped_column(String)
    series_uid:Mapped[str]=mapped_column(String)
    modality:Mapped[str]=mapped_column(String)
    frame_of_reference_uid:Mapped[str]=mapped_column(String)
    source_instances:Mapped[list]=mapped_column(JSON)
    geometry_summary:Mapped[dict]=mapped_column(JSON)
    eligibility:Mapped[str]=mapped_column(String)
    safe_error:Mapped[str|None]=mapped_column(String)
    warnings:Mapped[list]=mapped_column(JSON,default=list)

class Batch(Base):
    __tablename__='batches'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(String,index=True)
    scan_id:Mapped[str]=mapped_column(ForeignKey('scans.id'))
    created_by:Mapped[str]=mapped_column(String)
    approved_item_ids:Mapped[list]=mapped_column(JSON)
    normalized_config:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String,default='approved')
    cancel_requested_at:Mapped[float|None]=mapped_column(Float)

class Job(Base):
    __tablename__='jobs'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(String,index=True)
    result_key:Mapped[str]=mapped_column(String,unique=True)
    series_item_id:Mapped[str]=mapped_column(ForeignKey('series_items.id'))
    config:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String,default='queued')
    stage:Mapped[str]=mapped_column(String,default='queued')
    attempt_token:Mapped[str|None]=mapped_column(String)
    worker_id:Mapped[str|None]=mapped_column(String)
    lease_until:Mapped[float|None]=mapped_column(Float)
    cancel_requested:Mapped[bool]=mapped_column(Boolean,default=False)
    safe_error:Mapped[str|None]=mapped_column(String)
    artifacts:Mapped[dict]=mapped_column(JSON,default=dict)
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    retry_count:Mapped[int]=mapped_column(Integer,default=0)
    retry_at:Mapped[float|None]=mapped_column(Float)

class BatchJob(Base):
    __tablename__='batch_jobs'
    batch_id:Mapped[str]=mapped_column(ForeignKey('batches.id'),primary_key=True)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),primary_key=True)
    membership_status:Mapped[str]=mapped_column(String,default='active')
    reused:Mapped[bool]=mapped_column(Boolean,default=False)

class Attempt(Base):
    __tablename__='attempts'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    job_id:Mapped[str]=mapped_column(ForeignKey('jobs.id'),index=True)
    token:Mapped[str]=mapped_column(String,unique=True)
    worker_id:Mapped[str]=mapped_column(String)
    started_at:Mapped[float]=mapped_column(Float,default=time.time)
    heartbeat_at:Mapped[float]=mapped_column(Float,default=time.time)
    ended_at:Mapped[float|None]=mapped_column(Float)
    outcome:Mapped[str|None]=mapped_column(String)
    artifact_candidate:Mapped[dict]=mapped_column(JSON,default=dict)

class Outbox(Base):
    __tablename__='outbox'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    target_id:Mapped[str]=mapped_column(String,index=True)
    event_type:Mapped[str]=mapped_column(String)
    dispatch_status:Mapped[str]=mapped_column(String,default='pending')
    last_attempt_at:Mapped[float|None]=mapped_column(Float)
    available_at:Mapped[float]=mapped_column(Float,default=time.time)

class AuditEvent(Base):
    __tablename__='audit_events'
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    actor:Mapped[str]=mapped_column(String)
    tenant:Mapped[str]=mapped_column(String)
    action:Mapped[str]=mapped_column(String)
    target_id:Mapped[str]=mapped_column(String)
    timestamp:Mapped[float]=mapped_column(Float,default=time.time)

class Idempotency(Base):
    __tablename__='idempotency'
    __table_args__=(UniqueConstraint('tenant_id','actor_id','route','key'),)
    id:Mapped[str]=mapped_column(String,primary_key=True,default=new_id)
    tenant_id:Mapped[str]=mapped_column(String)
    actor_id:Mapped[str]=mapped_column(String)
    route:Mapped[str]=mapped_column(String)
    key:Mapped[str]=mapped_column(String)
    body_hash:Mapped[str]=mapped_column(String)
    response:Mapped[dict]=mapped_column(JSON)

class WorkerHealth(Base):
    __tablename__='worker_health'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    role:Mapped[str]=mapped_column(String)
    ready:Mapped[bool]=mapped_column(Boolean,default=False)
    heartbeat_at:Mapped[float]=mapped_column(Float)
    safe_error:Mapped[str|None]=mapped_column(String)
