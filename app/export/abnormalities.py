"""Validate and encode coarse model-attribution maps without resampling CT."""
import base64
import io
from pathlib import Path

import nibabel as nib
import numpy as np
from PIL import Image

from app.inference.ct_clip_adapter import ABNORMALITIES
from app.inference.rev_ct_heatmaps import METHOD
from app.storage.minio_store import file_sha


def heatmap_payload(path,native_path):
    try:
        with np.load(path,allow_pickle=False) as payload:
            maps=payload['maps'];affine=payload['affine_ras_mm'];maxima=payload['max_positive'];peaks=payload['peak_ras_mm']
            source=nib.load(native_path)
            if maps.dtype!=np.uint8 or maps.ndim!=4 or maps.shape[0]!=18 or not all(1<=n<=64 for n in maps.shape[1:]):raise ValueError
            if affine.shape!=(4,4) or not np.isfinite(affine).all() or abs(np.linalg.det(affine[:3,:3]))<1e-12:raise ValueError
            if not np.allclose(affine[3],[0,0,0,1]):raise ValueError
            if tuple(payload['source_shape'])!=source.shape or not np.allclose(payload['source_affine_ras_mm'],source.affine):raise ValueError
            if str(payload['source_sha256'])!=file_sha(Path(native_path)):raise ValueError
            if maxima.shape!=(18,) or not np.isfinite(maxima).all() or np.any(maxima<0) or peaks.shape!=(18,3):raise ValueError
            error=float(payload['reconstruction_error'])
            if not np.isfinite(error):raise ValueError
    except (KeyError,OSError,ValueError,TypeError):raise ValueError('REV_CT_INVALID_HEATMAP') from None
    encoded=[]
    for i,heatmap in enumerate(maps):
        # PNG rows concatenate z planes; each plane is y rows by x columns.
        atlas=heatmap.transpose(2,1,0).reshape(maps.shape[3]*maps.shape[2],maps.shape[1])
        buffer=io.BytesIO();Image.fromarray(atlas).save(buffer,format='PNG')
        encoded.append({'name':ABNORMALITIES[i],'png':base64.b64encode(buffer.getvalue()).decode('ascii'),
            'available':bool(maxima[i]>0),'peak_ras_mm':peaks[i].tolist() if np.isfinite(peaks[i]).all() else None})
    return {'schema_version':1,'kind':'model_attribution','method':METHOD,'model':'RevCT',
        'target':'positive contribution to the uncalibrated logit before feature-storage rounding',
        'normalization':'positive contributions divided by their per-finding maximum; uint8 0..255',
        'not_lesion_segmentation':True,'not_spatial_probability':True,
        'shape':list(maps.shape[1:]),'affine_ras_mm':affine.tolist(),
        'spacing_mm':np.linalg.norm(affine[:3,:3],axis=0).tolist(),
        'encoding':'grayscale PNG atlas; flat index x + nx*(y + ny*z)',
        'reconstruction_error':error,'maps':encoded}
