"""Generate fresh offline exports through the production generator; no inference.

The demonstration is a clearly marked synthetic phantom, not a patient result.
"""
import argparse
from pathlib import Path
import json

import nibabel as nib
import numpy as np

from app.export.preview import generate_preview
from app.inference.structures import TOTAL_LABELS
from tools.render_readme_preview import phantom


def write_export(root, image, masks, affine, labels, *, coded=True, context=None):
    root.mkdir(parents=True, exist_ok=True)
    ct = nib.Nifti1Image(image, affine)
    ct.header.set_xyzt_units('mm')
    segmentation = nib.Nifti1Image(masks, affine)
    segmentation.header.set_xyzt_units('mm')
    if not coded:
        ct.set_sform(None, code=0)
        ct.set_qform(None, code=0)
        segmentation.set_sform(None, code=0)
        segmentation.set_qform(None, code=0)
    nib.save(ct, root/'ct.nii.gz')
    nib.save(segmentation, root/'masks.nii.gz')
    generate_preview(root/'ct.nii.gz', root/'masks.nii.gz', root, labels,
                     standalone_only=True, viewer_context=context)
    viewer=root/'viewer.html'
    banner='<style>.viewer-app{height:calc(100dvh - 40px)}@media(max-width:980px){.viewer-app{height:auto}}</style><div style="padding:10px;text-align:center;background:#142031;color:white;direction:ltr;font:14px/20px Arial">SYNTHETIC DEMO · Illustrative voxels and masks · No patient data or model prediction</div>'
    viewer.write_text(viewer.read_text().replace('<body>', '<body>'+banner))
    return viewer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('work/viewer-verification'))
    args=parser.parse_args()
    image,masks=phantom()
    a=np.diag([1.4,1.4,2.5,1.]);a[:3,3]=[-179,-179,-60]
    labels={str(n):name for n,name in TOTAL_LABELS.items()}
    demo=write_export(args.output/'demo',image,masks,a,labels)
    shape=(18,16,12);m=np.zeros(shape,np.uint8);m[3:6,4:8,2:7]=1;m[11:14,4:8,2:7]=2;m[0,0,0]=3
    image=np.arange(np.prod(shape),dtype=np.int16).reshape(shape)-1000
    a=np.diag([2.,3.,4.,1.]);a[:3,3]=[31,-42,19]
    small={'1':'kidney_left','2':'kidney_right','3':'liver','4':'spleen','117':'unused'}
    write_export(args.output/'known',image,m,a,small)
    angle=.41;r=np.array([[1,0,0],[0,np.cos(angle),-np.sin(angle)],[0,np.sin(angle),np.cos(angle)]])
    oblique=a.copy();oblique[:3,:3]=r@a[:3,:3]
    write_export(args.output/'oblique',image,m,oblique,small)
    # Permutes the acquisition slice axis to display x; section navigation must still work.
    sagittal=a.copy();sagittal[:3,:3]=np.array([[0,0,4],[2,0,0],[0,3,0]])
    write_export(args.output/'permuted',image,m,sagittal,small)
    write_export(args.output/'missing',image,m,a,small,coded=False)
    print(json.dumps({'demo':str(demo.resolve()),'fixtures':['known','oblique','permuted','missing']}))


if __name__=='__main__':main()
