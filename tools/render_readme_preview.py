"""Capture the actual viewer using synthetic voxels and illustrative masks.

No patient files, service credentials, GPU inference or network assets are used.
Only the requested documentation screenshot is retained.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

import nibabel as nib
import numpy as np

from app.export.preview import generate_preview
from app.inference.structures import TOTAL_LABELS


def phantom():
    shape=(256,256,48)
    x,y,z=np.ogrid[-1:1:256j,-1:1:256j,-1:1:48j]
    rng=np.random.default_rng(42)
    noise=rng.normal(0,7,shape)
    image=np.full(shape,-1000,dtype=np.float32)
    masks=np.zeros(shape,dtype=np.uint8)
    body=np.broadcast_to((x/.9)**2+(y/.76)**2<1,shape)
    core=np.broadcast_to((x/.79)**2+(y/.64)**2<1,shape)
    image[body]=-95+noise[body]
    image[core]=30+noise[core]
    ids={name:number for number,name in TOTAL_LABELS.items()}

    def organ(name,center,radii,hu):
        region=sum(((axis-offset)/radius)**2 for axis,offset,radius in zip((x,y,z),center,radii))<1
        masks[region]=ids[name]
        image[region]=hu+noise[region]

    # Deliberately simplified phantom shapes: these are not model predictions.
    organ('liver',(-.36,.13,.08),(.36,.34,.8),62)
    organ('spleen',(.57,.13,.1),(.14,.28,.68),55)
    organ('stomach',(.22,.3,.2),(.2,.22,.6),10)
    organ('kidney_right',(-.36,-.28,-.05),(.14,.21,.55),48)
    organ('kidney_left',(.35,-.28,-.05),(.14,.21,.55),48)
    organ('iliopsoas_right',(-.17,-.43,0),(.085,.15,1.4),48)
    organ('iliopsoas_left',(.17,-.43,0),(.085,.15,1.4),48)
    organ('vertebrae_L2',(0,-.47,.14),(.105,.13,.42),650)
    organ('spinal_cord',(0,-.59,.14),(.035,.04,.42),35)
    organ('aorta',(.025,-.24,0),(.045,.048,1.4),155)
    organ('inferior_vena_cava',(-.09,-.21,0),(.047,.062,1.4),100)
    organ('pancreas',(.02,.03,.08),(.31,.075,.25),60)
    return np.rint(image).astype(np.int16),masks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('docs/images/viewer.png'))
    args=parser.parse_args()
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    work=Path('work');work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='readme-preview-',dir=work) as directory:
        root=Path(directory).resolve();results=root/'synthetic-demo/results';results.mkdir(parents=True)
        image,masks=phantom();affine=np.diag([1.4,1.4,2.5,1.]);affine[:3,3]=[-179,-179,-60]
        nib.save(nib.Nifti1Image(image,affine),root/'ct.nii.gz')
        nib.save(nib.Nifti1Image(masks,affine),root/'masks.nii.gz')
        generate_preview(root/'ct.nii.gz',root/'masks.nii.gz',results,
                         {str(n):name for n,name in TOTAL_LABELS.items()},standalone_only=True)
        viewer=results/'viewer.html'
        banner='<div style="padding:12px 24px;background:#142031;color:#fff;font:14px Arial;direction:ltr;text-align:center">SYNTHETIC DEMO · Illustrative shapes and masks · Not a patient scan or model prediction</div>'
        viewer.write_text(viewer.read_text().replace('<body>','<body>'+banner))
        # Virtual time allows embedded slice images to decode before capture.
        command=['chromium','--headless','--disable-gpu','--no-first-run','--no-default-browser-check',
                 '--disable-background-networking','--user-data-dir='+str(root/'profile'),
                 '--window-size=1600,1500','--virtual-time-budget=5000','--hide-scrollbars',
                 '--screenshot='+str(output),viewer.as_uri()]
        subprocess.run(command,check=True,capture_output=True,text=True,timeout=45)
        if not output.is_file():raise SystemExit('SCREENSHOT_NOT_CREATED')
        print('SYNTHETIC_VIEWER_SCREENSHOT',output)


if __name__=='__main__':main()
