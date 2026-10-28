"""Durable upload jobs on the GPU host; no database, Redis or object storage."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from app.inventory.uploads import validate_zip
from app.inference.structures import normalize_structures
from tools.common import atomic_write
from app.view_access import viewer_url,file_url



class LocalJobs:
    def __init__(self,settings):
        self.settings=settings
        self.root=settings.scratch/'local-uploads'

    def directory(self,id):
        if not re.fullmatch('[a-f0-9]{32}',id):raise ValueError('UPLOAD_NOT_FOUND')
        return self.root/id

    @staticmethod
    def request_id(actor,key):
        if not key or len(key)>128:raise ValueError('IDEMPOTENCY_KEY_REQUIRED')
        return hashlib.sha256(json.dumps([actor.tenant_id,actor.actor_id,key]).encode()).hexdigest()[:32]

    def progress(self,actor,key):
        id=self.request_id(actor,key)
        if not (self.directory(id)/'status.json').is_file():
            return {'job_id':id,'status':'receiving','stage':'receiving'}
        return self.public(self.authorized(id,actor))

    @contextmanager
    def lock(self,id,create=True):
        directory=self.directory(id)
        if create:directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (directory/'job.lock').open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            yield

    def load(self,id):
        path=self.directory(id)/'status.json'
        if not path.is_file():raise ValueError('UPLOAD_NOT_FOUND')
        return json.loads(path.read_text())

    def save(self,state,**updates):
        state.update(updates,updated_at=time.time())
        atomic_write(self.directory(state['job_id'])/'status.json',json.dumps(state))

    def authorized(self,id,actor):
        state=self.load(id)
        if state['tenant_id']!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
        return state

    def public(self,state):
        id=state['job_id']
        fields=('job_id','status','stage','created_at','updated_at','stage_started_at','timings','safe_error','series','selected_series_index','series_selection_policy','warnings','quality','structures','benchmark','reuse_segmentation')
        if state.get('ephemeral'):return {k:state[k] for k in fields if k in state}
        return {**{k:state[k] for k in fields if k in state},
                'status_url':f'/v1/uploads/{id}','result_url':f'/v1/uploads/{id}/result',
                'viewer_url':viewer_url(self.settings,id) if state['status']!='succeeded' or 'viewer' in state.get('artifacts',{}) else None,
                'archive_url':file_url(self.settings,id,'archive') if state['status']=='succeeded' and 'archive' in state.get('artifacts',{}) else None}

    def listing(self,actor):
        rows=[]
        for path in self.root.glob('*/status.json'):
            state=json.loads(path.read_text())
            if state['tenant_id']==actor.tenant_id:rows.append(self.public(state))
        return sorted(rows,key=lambda s:s['created_at'],reverse=True)

    def submit(self,stream,actor,key,structures,quality,reuse_segmentation=True,auto_select_series=False,series_index=None,ephemeral=False,wait_timeout=3600):
        if not actor.operator:raise ValueError('ACCESS_DENIED')
        if not key or len(key)>128:raise ValueError('IDEMPOTENCY_KEY_REQUIRED')
        structures=normalize_structures(structures)
        if quality not in ('standard','fast'):
            raise ValueError('INVALID_CONFIG')
        if ephemeral:reuse_segmentation=False
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with tempfile.TemporaryDirectory(prefix='.incoming-',dir=self.root) as tmp:
            path=Path(tmp)/'input.zip';sha=hashlib.sha256();size=0
            with path.open('xb') as target:
                while block:=stream.read(1024*1024):
                    size+=len(block)
                    if size>self.settings.max_archive:raise ValueError('ARCHIVE_LIMIT')
                    sha.update(block);target.write(block)
            validate_zip(path,self.settings)
            request={'sha256':sha.hexdigest(),'structures':structures,'quality':quality}
            if not reuse_segmentation:request['reuse_segmentation']=False
            if series_index is not None:request['series_index']=series_index
            elif auto_select_series:request['auto_select_series']=True
            digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
            id=self.request_id(actor,key)
            with self.lock(id):
                if (self.directory(id)/'status.json').exists():
                    state=self.authorized(id,actor)
                    if state['request_digest']!=digest:raise ValueError('IDEMPOTENCY_CONFLICT')
                    if ephemeral:raise ValueError('UPLOAD_IN_PROGRESS')
                    return self.public(state)
                os.replace(path,self.directory(id)/'input.zip')
                state={'job_id':id,'tenant_id':actor.tenant_id,'actor_id':actor.actor_id,
                       'request_digest':digest,'input_sha256':sha.hexdigest(),'input_bytes':size,
                       'created_at':time.time(),'structures':structures,'quality':quality,'reuse_segmentation':reuse_segmentation,
                       'auto_select_series':auto_select_series,'requested_series_index':series_index}
                if ephemeral:state.update(ephemeral=True,viewer_only=True,expires_at=time.time()+wait_timeout)
                self.save(state,status='queued',stage='queued',safe_error=None)
                return self.public(state)

    def discard(self,id,actor=None):
        """Remove idle work now; let the worker cancel and remove active work."""
        if not (self.directory(id)/'status.json').exists():return
        try:
            with self.lock(id,create=False):
                if not (self.directory(id)/'status.json').is_file():return
                state=self.authorized(id,actor) if actor else self.load(id)
                if state['status'] in ('queued','awaiting_selection','succeeded','failed'):
                    shutil.rmtree(self.directory(id))
                else:(self.directory(id)/'.discard').touch(mode=0o600)
        except FileNotFoundError:pass

    def claim(self,id):
        """Claim a queued request while coordinating with API-side deletion."""
        try:
            with self.lock(id,create=False):
                if not (self.directory(id)/'status.json').is_file():return None
                state=self.load(id)
                if state['status']!='queued':return None
                self.save(state,status='validating',stage='validating')
                return state
        except FileNotFoundError:return None

    def collect_viewer(self,id,actor):
        """Copy the sole response to RAM and erase all source/intermediate files."""
        with self.lock(id,create=False):
            state=self.authorized(id,actor)
            path=self.artifact(id,actor,'viewer')
            content=path.read_bytes()
            shutil.rmtree(self.directory(id))
            return content,state

    def reap_ephemeral(self,abandon_all=False):
        if abandon_all:
            # These can remain if the API was killed during upload or before
            # its first status file was atomically committed.
            for path in self.root.iterdir() if self.root.exists() else ():
                if path.is_dir() and (path.name.startswith('.incoming-') or
                    re.fullmatch('[a-f0-9]{32}',path.name) and not (path/'status.json').exists()):
                    shutil.rmtree(path,ignore_errors=True)
        for path in self.root.glob('*/status.json'):
            try:
                state=json.loads(path.read_text())
                if state.get('ephemeral') and (abandon_all or state.get('expires_at',0)<=time.time()):
                    self.discard(state['job_id'])
            except FileNotFoundError:pass  # API/worker may have just completed cleanup.

    def select(self,id,actor,index):
        if not actor.operator:raise ValueError('ACCESS_DENIED')
        with self.lock(id):
            state=self.authorized(id,actor)
            if state.get('selected_series_index')==index:return self.public(state)
            if state['status']!='awaiting_selection':raise ValueError('UPLOAD_NOT_AWAITING_SELECTION')
            if not any(s['index']==index and s['eligibility']=='eligible' and s['modality']=='CT' for s in state['series']):
                raise ValueError('ITEM_NOT_ELIGIBLE')
            self.save(state,selected_series_index=index,status='queued',stage='queued')
            return self.public(state)

    def result(self,id,actor):
        state=self.authorized(id,actor)
        if state['status']!='succeeded':raise ValueError('RESULT_NOT_READY')
        result=json.loads((self.directory(id)/'result.json').read_text())
        public=self.public(state)
        return {**result,'benchmark':state.get('benchmark',result.get('benchmark',{})),'job_id':id,'status':'succeeded','viewer_url':public['viewer_url'],'archive_url':public['archive_url'],'artifacts':[
            {'artifact_id':name,'path':f'/v1/uploads/{id}/artifacts/{name}'} for name in state['artifacts']]}

    def artifact(self,id,actor,name):
        state=self.authorized(id,actor)
        if state['status']!='succeeded':raise ValueError('RESULT_NOT_READY')
        # Lookup only known artifact IDs; request data never becomes a file path.
        filename=state.get('artifacts',{}).get(name)
        if not filename:raise ValueError('ARTIFACT_NOT_FOUND')
        path=self.directory(id)/'results'/filename
        if not path.is_file():raise ValueError('ARTIFACT_NOT_FOUND')
        return path
