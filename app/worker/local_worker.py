"""Consume disk-backed upload jobs and run GPU inference entirely on this host."""
import fcntl
import json
from pathlib import Path
import shutil
import time
import warnings

import nibabel as nib
import numpy as np

from app.config import Settings
from app.local_jobs import LocalJobs
from app.inventory.archive import extract_zip
from app.inventory.dicom_series import inventory_paths
from app.geometry.native_grid import build_native_volume,resample_masks_to_native
from app.inference.totalseg_adapter import run_segmentation
from app.inference.local_cache import cached_segmentation
from app.export.dicom_seg import export_labelmap_seg
from app.export.measurements import measure_labelmap
from app.inference.structures import TOTAL_LABELS
from app.export.preview import generate_preview
from app.export.portable import create_portable_package
from app.worker.tasks import gpu_slot


def process(jobs,state):
    settings=jobs.settings;directory=jobs.directory(state['job_id']);started=time.monotonic()
    timings={};current_stage=None;phase_started=started
    def mark(name,status):
        nonlocal current_stage,phase_started
        if state.get('ephemeral') and ((directory/'.discard').exists() or time.time()>=state['expires_at']):
            raise ValueError('UPLOAD_ABORTED')
        now=time.monotonic()
        if current_stage:timings[current_stage]=round(timings.get(current_stage,0)+now-phase_started,3)
        current_stage=name;phase_started=now
        jobs.save(state,status=status,stage=name,stage_started_at=time.time(),timings=dict(timings))
    try:
        mark('validating','validating')
        extracted=directory/'extracted'
        if extracted.exists():shutil.rmtree(extracted)
        paths=extract_zip(directory/'input.zip',extracted,settings.max_expanded,settings.max_entries,settings.max_ratio)
        series=inventory_paths(paths)
        summaries=[{'index':i,'modality':s.modality,'eligibility':s.eligibility,'safe_error':s.safe_error,
                    'slices':s.geometry_summary.get('slices')} for i,s in enumerate(series)]
        eligible=[i for i,s in enumerate(series) if s.eligibility=='eligible' and s.modality=='CT']
        jobs.save(state,series=summaries)
        if not eligible:raise ValueError('NO_ELIGIBLE_CT_SERIES')
        index=state.get('selected_series_index',state.get('requested_series_index'))
        if index is None:
            if state.get('auto_select_series'):
                index=max(eligible,key=lambda i:(series[i].geometry_summary['slices'],-i))
            elif len(eligible)>1:
                jobs.save(state,status='awaiting_selection',stage='awaiting_selection')
                return
            else:index=eligible[0]
        if index not in eligible:raise ValueError('ITEM_NOT_ELIGIBLE')
        policy='explicit' if state.get('requested_series_index') is not None else 'most_slices' if state.get('auto_select_series') else 'manual'
        jobs.save(state,selected_series_index=index,series_selection_policy=policy)
        config={'task':'total','structures':state['structures'],'quality':state['quality'],
                'model_release':settings.model_release,'pipeline_version':settings.pipeline_version,
                'model_cache':str(settings.model_cache),'checkpoint_digest':settings.checkpoint_digest,
                'timeout_seconds':settings.timeout_seconds,'multilabel_output':True}
        volume=build_native_volume(series[index])
        cached=cached_segmentation(jobs,state,config,volume)
        if cached:
            mark('reusing_segmentation','running')
            model=directory/'model';model.mkdir(exist_ok=True)
            shutil.copyfile(cached/'results/ct.nii.gz',model/'native.nii.gz')
            shutil.copyfile(cached/'results/masks.nii.gz',model/'model-labels.nii.gz')
            (model/'model-config.json').write_text(json.dumps(config))
            (model/'model-metrics.json').write_text(json.dumps({'model_wall_seconds':0,'cache_hit':True,'reused_from_job_id':cached.name}))
            native=nib.load(model/'model-labels.nii.gz')
        else:
            mark('waiting_gpu','waiting_gpu')
            while True:
                try:
                    with gpu_slot(settings.scratch) as fd:
                        mark('running','running')
                        output=run_segmentation(volume,config,directory/'model',
                            lease_guard=lambda:not state.get('ephemeral') or (not (directory/'.discard').exists() and time.time()<state['expires_at']),gpu_lock_fd=fd)
                    break
                except ValueError as exc:
                    if str(exc)!='GPU_BUSY':raise
                    mark('waiting_gpu','waiting_gpu');time.sleep(1)
            mark('resampling','exporting')
            from nibabel.processing import resample_from_to
            native=resample_from_to(output,(volume.array.shape,volume.affine_ras),order=0)
        mark('measurements','exporting')
        labelmap=np.asanyarray(native.dataobj).astype(np.uint8)
        labels={number:name for number,name in TOTAL_LABELS.items() if name in state['structures']}
        results=directory/'results';results.mkdir(exist_ok=True,mode=0o700)
        if state.get('html_only'):
            # Only the HTML needs a temporary mask. No ZIP, DICOM SEG, PNG,
            # standalone CSS, measurements file or reusable result is produced.
            nib.save(nib.Nifti1Image(labelmap,volume.affine_ras),results/'masks.nii.gz')
            mark('creating_preview','exporting')
            artifacts=generate_preview(directory/'model/native.nii.gz',results/'masks.nii.gz',results,
                                       {str(i):label for i,label in labels.items()},standalone_only=True)
            timings[current_stage]=round(time.monotonic()-phase_started,3)
            shutil.rmtree(extracted)
            jobs.save(state,status='succeeded',stage='succeeded',artifacts=artifacts,timings=dict(timings))
            return
        measurements=measure_labelmap(labelmap,labels,volume)
        (results/'measurements.json').write_text(json.dumps(measurements))
        nib.save(nib.Nifti1Image(labelmap,volume.affine_ras),results/'masks.nii.gz')
        artifacts={'masks':'masks.nii.gz','measurements':'measurements.json','archive':'results.zip'}
        notes=[]
        mark('dicom_export','exporting')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning)
                export_labelmap_seg(labelmap,labels,volume,config,results/'segmentation.dcm',max_bytes=settings.max_artifact)
            artifacts['seg']='segmentation.dcm'
        except Exception:
            (results/'segmentation.dcm').unlink(missing_ok=True)
            notes.append('DICOM_SEG_EXPORT_UNAVAILABLE')
        metrics_path=directory/'model/model-metrics.json'
        benchmark=json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
        result={**measurements,'model_release':settings.model_release,'quality':state['quality'],
                'labels':{str(i):label for i,label in labels.items()},'warnings':notes,
                'benchmark':{**benchmark,'wall_seconds':time.monotonic()-started}}
        mark('creating_preview','exporting')
        try:
            artifacts.update(generate_preview(directory/'model/native.nii.gz',results/'masks.nii.gz',results,result['labels']))
        except Exception:
            notes.append('LOCAL_PREVIEW_UNAVAILABLE')
        result['benchmark']['wall_seconds']=time.monotonic()-started
        (directory/'result.json').write_text(json.dumps(result))
        mark('creating_archive','exporting')
        def finalize_result():
            result['benchmark'].update(wall_seconds=time.monotonic()-started,
                stage_seconds={**timings,current_stage:round(time.monotonic()-phase_started,3)})
            return result
        artifacts.update(create_portable_package(directory,artifacts,series[index].paths,finalize_result=finalize_result))
        timings[current_stage]=round(time.monotonic()-phase_started,3)
        result['benchmark'].update(wall_seconds=time.monotonic()-started,stage_seconds=dict(timings))
        jobs.save(state,status='succeeded',stage='succeeded',artifacts=artifacts,warnings=notes,timings=dict(timings),benchmark=result['benchmark'])
        shutil.rmtree(extracted,ignore_errors=True)
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and str(exc).isupper() and len(str(exc))<80 else 'LOCAL_PROCESSING_FAILED'
        jobs.save(state,status='failed',stage='failed',safe_error=code)
    finally:
        # A disconnected/timed-out request has no receiver. The worker owns
        # cleanup until inference exits, so files are never removed under it.
        if state.get('ephemeral') and ((directory/'.discard').exists() or time.time()>=state['expires_at']):
            shutil.rmtree(directory,ignore_errors=True)


def main():
    jobs=LocalJobs(Settings.from_env());jobs.root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (jobs.root/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('LOCAL_WORKER_ALREADY_RUNNING') from None
        for path in jobs.root.glob('*/status.json'):
            try:
                with jobs.lock(path.parent.name,create=False):
                    if not path.is_file():continue
                    state=jobs.load(path.parent.name)
                    if state['status'] in ('validating','waiting_gpu','running','exporting'):
                        jobs.save(state,status='queued',stage='queued',safe_error=None)
            except FileNotFoundError:continue
        while True:
            jobs.reap_ephemeral()
            queued=[]
            for path in jobs.root.glob('*/status.json'):
                try:state=json.loads(path.read_text())
                except FileNotFoundError:continue
                if state['status']=='queued':queued.append(state)
            if queued:
                state=min(queued,key=lambda s:s['created_at'])
                # Claim under the same lock as discard before using its files.
                state=jobs.claim(state['job_id'])
                if state:process(jobs,state)
            else:time.sleep(.5)


if __name__=='__main__':main()
