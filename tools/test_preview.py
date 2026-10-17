"""Preview CT and mask must receive identical orientation changes."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest

import nibabel as nib
import numpy as np
from PIL import Image

from app.export.preview import generate_preview


class PreviewTests(unittest.TestCase):
    def test_voxel_alignment_and_offline_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ct=np.arange(24,dtype=np.float32).reshape(3,4,2)
            mask=np.zeros(ct.shape,dtype=np.uint8);mask[0,1,0]=1;mask[2,2,1]=85
            nib.save(nib.Nifti1Image(ct,np.eye(4)),root/'ct.nii.gz')
            nib.save(nib.Nifti1Image(mask,np.eye(4)),root/'mask.nii.gz')
            generate_preview(root/'ct.nii.gz',root/'mask.nii.gz',root,{'1':'spleen','85':'test_region','117':'empty_region'})
            html=(root/'viewer.html').read_text()
            data=json.loads(html.split('const data=',1)[1].split(';const byId',1)[0])
            image=np.asarray(Image.open(io.BytesIO(base64.b64decode(data['frames'][0]))))
            overlay=np.asarray(Image.open(io.BytesIO(base64.b64decode(data['overlays'][0]))))
            decoded=(image[:,:,0].astype(np.uint16)+256*image[:,:,1].astype(np.uint16))*data['hu_step']+data['hu_offset']
            np.testing.assert_array_equal(decoded,ct[::-1,::-1,0].T)
            np.testing.assert_array_equal(overlay,mask[::-1,::-1,0].T)
            self.assertEqual(data['present'],[1,85])
            self.assertIn('85',data['colors'])
            self.assertNotIn('<script src=',html)
            self.assertEqual(data['start'],0)
            self.assertEqual(data['labels_fa']['1'],'طحال')
            self.assertIn('--color-primary: #0065f8',html)
            self.assertIn(':root[data-app-key="isarmed"]',html)
            self.assertIn(':root[data-app-key="lungimed"]',html)
            self.assertIn('id="themePicker"',html)
            self.assertNotIn('@theme',html)
            self.assertNotIn('@apply',html)
            self.assertTrue((root/'theme.css').is_file())
            self.assertEqual(data['hu_step'],1)
            self.assertTrue((root/'preview.png').is_file())

    def test_fractional_hu_roundtrip_and_anatomical_translations(self):
        from app.export.persian import persian_name
        from app.inference.structures import TOTAL_LABELS
        self.assertTrue(all(persian_name(name)!=name.replace('_',' ') for name in TOTAL_LABELS.values()))
        self.assertEqual(persian_name('vertebrae_L5'),'مهرهٔ کمری 5')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ct=np.array([-1024.2,40.3,120.8,3000.1],dtype=np.float32).reshape(2,2,1)
            nib.save(nib.Nifti1Image(ct,np.eye(4)),root/'ct.nii.gz')
            nib.save(nib.Nifti1Image(np.ones(ct.shape,dtype=np.uint8),np.eye(4)),root/'mask.nii.gz')
            generate_preview(root/'ct.nii.gz',root/'mask.nii.gz',root,{'1':'spleen'})
            data=json.loads((root/'viewer.html').read_text().split('const data=',1)[1].split(';const byId',1)[0])
            image=np.asarray(Image.open(io.BytesIO(base64.b64decode(data['frames'][0]))))
            decoded=(image[:,:,0].astype(np.uint16)+256*image[:,:,1].astype(np.uint16))*data['hu_step']+data['hu_offset']
            np.testing.assert_allclose(decoded,ct[::-1,::-1,0].T,atol=data['hu_step']/2+1e-4)

    def test_misaligned_mask_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ct=np.zeros((2,2,2),dtype=np.uint8)
            nib.save(nib.Nifti1Image(ct,np.eye(4)),root/'ct.nii.gz')
            moved=np.eye(4);moved[0,3]=1
            nib.save(nib.Nifti1Image(ct,moved),root/'mask.nii.gz')
            with self.assertRaisesRegex(ValueError,'MASK_GEOMETRY_MISMATCH'):
                generate_preview(root/'ct.nii.gz',root/'mask.nii.gz',root,{'1':'liver'})


if __name__=='__main__':unittest.main()
