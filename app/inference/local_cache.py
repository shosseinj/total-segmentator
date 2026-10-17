"""Reuse only verified local masks for identical input, series and model settings."""
import json

import nibabel as nib
import numpy as np

from app.export.portable import file_digest


def cached_segmentation(jobs,state,config,volume):
    if not state.get('reuse_segmentation',True):return None
    candidates=[]
    for path in jobs.root.glob('*/status.json'):
        try:
            previous=json.loads(path.read_text())
            if (previous['job_id']!=state['job_id'] and previous['status']=='succeeded'
                and previous['tenant_id']==state['tenant_id'] and previous['input_sha256']==state['input_sha256']
                and previous.get('selected_series_index')==state['selected_series_index']):candidates.append((path.parent,previous))
        except (OSError,ValueError,KeyError):continue
    for root,previous in sorted(candidates,key=lambda item:item[1]['updated_at'],reverse=True):
        try:
            old=json.loads((root/'model/model-config.json').read_text())
            if any(old.get(key)!=config[key] for key in ('task','quality','model_release','pipeline_version','checkpoint_digest')):continue
            if set(old['structures'])!=set(config['structures']):continue
            manifest=json.loads((root/'results/manifest.json').read_text())
            if [item['sop_instance_uid'] for item in manifest['source']['instances']]!=volume.source_sops:continue
            for name in ('ct.nii.gz','masks.nii.gz'):
                path=root/'results'/name;entry=manifest['files'][name]
                if path.stat().st_size!=entry['size_bytes'] or file_digest(path)!=entry['sha256']:break
            else:
                ct=nib.load(root/'results/ct.nii.gz');mask=nib.load(root/'results/masks.nii.gz')
                if ct.shape==mask.shape==volume.array.shape and np.allclose(ct.affine,volume.affine_ras) and np.allclose(mask.affine,volume.affine_ras):return root
        except (OSError,ValueError,KeyError,TypeError,nib.filebasedimages.ImageFileError):continue
    return None
