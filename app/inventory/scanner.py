from app.contracts import ObjectSnapshot

def list_snapshots(store,bucket,prefix,max_objects,allowed_prefix,object_keys=None):
    if not prefix.startswith(allowed_prefix) or max_objects<1 or max_objects>20:raise ValueError('SCOPE_DENIED')
    if object_keys is not None:
        if len(object_keys)>max_objects or any(not k.startswith(prefix) for k in object_keys):raise ValueError('SCOPE_DENIED')
        return [store.snapshot(bucket,k) for k in dict.fromkeys(object_keys) if store.is_zip(bucket,k)]
    rows=[];cursor=None
    while True:
        page,cursor=store.list_page(bucket,prefix,cursor)
        for r in page:
            if not r['key'].lower().endswith('.zip'):continue
            snap=store.snapshot(bucket,r['key']) if hasattr(store,'snapshot') else ObjectSnapshot(bucket=bucket,**r)
            rows.append(snap)
            if len(rows)==max_objects:return rows
        if not cursor:return rows

def run_scan(scan_id,repository,store,settings):
    import fcntl
    directory=settings.scratch/'inventory-locks'
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/(scan_id+'.lock')).open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        try:return _run_scan(scan_id,repository,store,settings)
        finally:fcntl.flock(lock,fcntl.LOCK_UN)

def _run_scan(scan_id,repository,store,settings):
    """Inventory only: no inference import or GPU queue call exists here."""
    import errno,shutil,time
    from dataclasses import asdict
    from sqlalchemy import select
    from app.db.models import Scan,ScanObject,Series,Outbox
    from app.jobs.recovery import DELAYS
    from app.storage.minio_store import download_snapshot
    from app.inventory.archive import extract_zip
    from app.inventory.dicom_series import inventory_paths
    with repository.session.begin() as db:
        scan=db.get(Scan,scan_id,with_for_update=True)
        if not scan or scan.status not in ('queued','scanning'):return
        scan.status='scanning';scope=scan.requested_scope
    try:
        with repository.session() as db:
            persisted=db.scalars(select(ScanObject).where(ScanObject.scan_id==scan_id)).all()
        snapshots=[ObjectSnapshot(**o.snapshot) for o in persisted] if persisted else list_snapshots(store,settings.input_bucket,scope['prefix'],scope['max_objects'],settings.allowed_prefix,scope.get('object_keys'))
        if not persisted:
            with repository.session.begin() as db:
                for snapshot in snapshots:db.add(ScanObject(scan_id=scan_id,snapshot=asdict(snapshot)))
        for snapshot in snapshots:
            with repository.session.begin() as db:
                # A redelivered scan must not append the same object twice.
                existing=db.scalars(select(ScanObject).where(ScanObject.scan_id==scan_id)).all()
                obj=next((o for o in existing if o.snapshot['key']==snapshot.key),None)
                if obj and obj.inventory_status!='pending':continue
                if obj and obj.retry_at and obj.retry_at>time.time():continue
                if obj is None:
                    obj=ScanObject(scan_id=scan_id,snapshot=asdict(snapshot));db.add(obj);db.flush()
                else:snapshot=ObjectSnapshot(**obj.snapshot)
                object_id=obj.id
            work=settings.scratch/'inventory'/scan_id/object_id
            try:
                shutil.rmtree(work,ignore_errors=True)
                downloaded=download_snapshot(store,snapshot,work/'input.zip',settings.max_archive)
                with repository.session.begin() as db:db.get(ScanObject,object_id).snapshot=asdict(snapshot)
                files=extract_zip(downloaded.path,work/'extracted',settings.max_expanded,settings.max_entries,settings.max_ratio)
                items=inventory_paths(files)
                with repository.session.begin() as db:
                    obj=db.get(ScanObject,object_id);obj.snapshot=asdict(snapshot);obj.inventory_status='completed' if items else 'invalid';obj.safe_error=None if items else 'NO_DICOM_SERIES';obj.retry_at=None
                    for item in items:
                        db.add(Series(scan_object_id=object_id,study_uid=item.study_uid,series_uid=item.series_uid,modality=item.modality,frame_of_reference_uid=item.frame_of_reference_uid,source_instances=item.source_instances,geometry_summary=item.geometry_summary,eligibility=item.eligibility,safe_error=item.safe_error,warnings=item.warnings))
            except Exception as exc:
                known={'INPUT_CHANGED','ARCHIVE_LIMIT','UNSAFE_ARCHIVE','CORRUPT_ZIP','ENCRYPTED_ZIP'}
                code=str(exc) if str(exc) in known else ('DISK_FULL' if isinstance(exc,OSError) and exc.errno==errno.ENOSPC else 'INVENTORY_IO_FAILED')
                with repository.session.begin() as db:
                    obj=db.get(ScanObject,object_id);obj.safe_error=code
                    if code not in known and obj.retry_count<len(DELAYS):
                        obj.retry_at=time.time()+DELAYS[obj.retry_count];obj.retry_count+=1
                        db.add(Outbox(target_id=scan_id,event_type='inventory',available_at=obj.retry_at))
                    else:obj.inventory_status='failed';obj.retry_at=None
            finally:shutil.rmtree(work,ignore_errors=True)
        with repository.session.begin() as db:
            items=db.scalars(select(Series).join(ScanObject).where(ScanObject.scan_id==scan_id)).all()
            objects=db.scalars(select(ScanObject).where(ScanObject.scan_id==scan_id)).all()
            counters={k:sum(i.eligibility==k for i in items) for k in ['eligible','invalid','unsupported']}
            counters.update(objects=len(objects),failed=sum(o.inventory_status=='failed' for o in objects))
            scan=db.get(Scan,scan_id);scan.counters=counters
            scan.status='scanning' if any(o.inventory_status=='pending' for o in objects) else 'completed'
            if scan.status=='completed':scan.completed_at=time.time()
    except Exception:
        with repository.session.begin() as db:
            scan=db.get(Scan,scan_id);scan.status='failed';scan.safe_error='SCAN_IO_FAILED'
