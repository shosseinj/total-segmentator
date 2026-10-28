"""Lossless CT/mask viewer package from real voxels; no external services."""
import base64
import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import nibabel as nib
import numpy as np
from PIL import Image

from tools.common import atomic_write
from app.inference.structures import label_colors, TOTAL_LABELS
from app.export.persian import persian_name
from app.export.theme import browser_theme
from app.export.viewer_geometry import viewer_geometry, original_fingerprint
from app.export.viewer_precompute import precompute_viewer
from app.export.viewer_package import build_viewer_package


def png_data(array):
    stream=io.BytesIO()
    Image.fromarray(array).save(stream,format='PNG')
    return base64.b64encode(stream.getvalue()).decode('ascii')


def generate_preview(native_path,masks_path,directory,labels,standalone_only=False,viewer_context=None):
    directory=Path(directory)
    ct=nib.load(native_path);mask=nib.load(masks_path)
    if ct.shape!=mask.shape or not np.allclose(ct.affine,mask.affine):
        raise ValueError('MASK_GEOMETRY_MISMATCH')
    # Apply exactly the same voxel permutation/flips to CT and masks. LPS gives
    # radiological axial display: patient R at image left, A at image top.
    transform=nib.orientations.ornt_transform(nib.orientations.io_orientation(ct.affine),nib.orientations.axcodes2ornt(('L','P','S')))
    image=nib.orientations.apply_orientation(np.asanyarray(ct.dataobj),transform)
    segmentation=nib.orientations.apply_orientation(np.asanyarray(mask.dataobj).astype(np.uint8),transform)
    affine=ct.affine@nib.orientations.inv_ornt_aff(transform,ct.shape)
    geometry=viewer_geometry(ct,affine,transform,viewer_context)
    context=viewer_context or {}
    windowed=np.round(np.clip((image+160)/400,0,1)*255).astype(np.uint8)
    # Lossless integer HU, or a documented bounded quantization for fractional HU.
    # RGB PNG uses R/G for low/high bytes and opaque alpha on browser decode.
    hu_offset=float(np.min(image));hu_range=float(np.max(image))-hu_offset
    hu_step=1.0 if hu_range<=65535 and np.all(image==np.rint(image)) else max(hu_range/65535,.001)
    colors=label_colors(labels)
    def frame(index):
        encoded=np.rint((image[:,:,index].T-hu_offset)/hu_step).clip(0,65535).astype(np.uint16)
        packed=np.zeros((*encoded.shape,3),dtype=np.uint8)
        packed[:,:,0]=encoded&255;packed[:,:,1]=encoded>>8
        pixels=segmentation[:,:,index].T
        return png_data(packed),png_data(pixels),np.bincount(pixels.ravel(),minlength=256)
    # PNG compression releases the GIL; keep exact pixels and compression while
    # using a bounded number of CPU threads rather than serially encoding slices.
    with ThreadPoolExecutor(max_workers=min(4,os.cpu_count() or 1)) as pool:
        encoded=list(pool.map(frame,range(image.shape[2])))
    frames=[item[0] for item in encoded];overlays=[item[1] for item in encoded]
    areas=np.stack([item[2] for item in encoded]);totals=areas.sum(axis=0)
    present=[int(n) for n in np.flatnonzero(totals) if n and str(int(n)) in labels]
    counts=areas[:,1:].sum(axis=1)
    selected=int(np.argmax(counts)) if counts.any() else image.shape[2]//2
    payload={'frames':frames,'overlays':overlays,'start':selected,'labels':labels,
             'job_id':directory.parent.name,
             'has_archive':not standalone_only,'has_ct':not standalone_only and (directory.parent/'model/native.nii.gz').is_file(),
             'labels_fa':{n:persian_name(name) for n,name in labels.items()},
             'hu_offset':hu_offset,'hu_step':hu_step,
             'volumes_ml':{str(n):int(totals[n])*abs(float(np.linalg.det(affine[:3,:3])))/1000 for n in present},
             'colors':colors,'present':present,
             'region_slices':{str(n):int(np.argmax(areas[:,n])) for n in present},
             'spacing':np.linalg.norm(affine[:3,:3],axis=0).tolist(),
             'has_dicom':(directory/'segmentation.dcm').is_file(),
             'positions':[round(float((affine@np.array([0,0,z,1]))[2]),1) for z in range(image.shape[2])],
             'shape':list(image.shape)}
    payload.update(geometry=geometry,available_class_count=len(TOTAL_LABELS),
        source_identity={'result_id':directory.parent.name, **context.get('source_identity',{})},
        authenticated_user=context.get('authenticated_user'),
        original_fingerprint=original_fingerprint(image,segmentation,geometry))
    payload['precomputed']=precompute_viewer(image,segmentation,geometry,labels,hu_offset,hu_step)
    package=build_viewer_package(payload,directory)
    if standalone_only:return {'viewer':package.name}
    theme=browser_theme()
    atomic_write(directory/'theme.css',theme)
    static_preview(windowed,segmentation,selected,directory/'preview.png',colors)
    return {'viewer':package.name,'preview':'preview.png','theme':'theme.css'}


def static_preview(image,mask,index,path,colors):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,6),facecolor='#10151d')
    for ax,title in zip(axes,('Original CT','Automatic segmentation overlay')):
        ax.imshow(image[:,:,index].T,cmap='gray',vmin=0,vmax=255,interpolation='nearest')
        ax.set_title(title,color='white',pad=12);ax.axis('off')
    overlay=np.zeros((*mask[:,:,index].T.shape,4),dtype=np.float32)
    for number,color in colors.items():overlay[mask[:,:,index].T==number]=[*[c/255 for c in color],.45]
    axes[1].imshow(overlay,interpolation='nearest')
    fig.suptitle(f'Axial slice {index+1} / {image.shape[2]} · unreviewed',color='#aeb9c8',fontsize=11)
    fig.tight_layout();fig.savefig(path,dpi=130,facecolor=fig.get_facecolor());plt.close(fig)
    Path(path).chmod(0o600)
