"""Geometry/provenance additions to the real standalone HTML generator."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import nibabel as nib
import numpy as np

from app.export.preview import generate_preview


class ViewerGeometryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.image=np.arange(9*8*7,dtype=np.int16).reshape(9,8,7)
        self.mask=np.zeros(self.image.shape,np.uint8)
        self.mask[2:5,2:6,2:5]=1
        self.affine=np.diag([2.,3.,4.,1.])
        self.affine[:3,3]=[31,-42,19]

    def write(self, *, units='mm', coded=True, affine=None):
        for name,array in [('ct',self.image),('mask',self.mask)]:
            img=nib.Nifti1Image(array,self.affine if affine is None else affine)
            img.header.set_xyzt_units(units)
            if not coded:img.set_sform(None,code=0);img.set_qform(None,code=0)
            nib.save(img,self.root/(name+'.nii.gz'))

    def generate(self, context=None):
        generate_preview(self.root/'ct.nii.gz',self.root/'mask.nii.gz',self.root,
                         {'1':'kidney_left','2':'kidney_right'},standalone_only=True,viewer_context=context)
        with zipfile.ZipFile(next(self.root.glob('ct-viewer-*.zip'))) as archive:
            html=archive.read('viewer/index.html').decode()
            payload=json.loads(archive.read('viewer/data/manifest.json'))
        return payload,html

    def test_affine_metadata_matches_exact_permuted_original_voxels(self):
        angle=.4;r=np.array([[1,0,0],[0,np.cos(angle),-np.sin(angle)],[0,np.sin(angle),np.cos(angle)]])
        a=self.affine.copy();a[:3,:3]=r@a[:3,:3];self.write(affine=a)
        before={name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in ['ct.nii.gz','mask.nii.gz']}
        data,html=self.generate()
        g=data['geometry'];self.assertTrue(g['valid']);self.assertFalse(g['section_is_patient_axial'])
        np.testing.assert_allclose(np.array(g['original_affine_ras'])@np.array(g['voxel_transform']),g['affine_ras_mm'])
        self.assertEqual(g['section_definition'],'native_acquisition_planes_no_resampling')
        self.assertIsNone(data['authenticated_user']);self.assertTrue(data['original_fingerprint'].startswith('sha256:'))
        for name,digest in before.items():self.assertEqual(hashlib.sha256((self.root/name).read_bytes()).hexdigest(),digest)
        for panel in ['measurementPanel','editingPanel','mprPanel','surfacePanel']:
            self.assertIn('id="'+panel+'"',html)
        for token in ['__WORKER_JS__','__EXTENSION_JS__','__DATA__','data:image/']:
            self.assertNotIn(token,html)
        self.assertEqual(self.generate()[0]['original_fingerprint'],data['original_fingerprint'])

    def test_uncoded_orientation_or_unknown_units_are_not_invented(self):
        self.write(coded=False)
        data,_=self.generate();self.assertFalse(data['geometry']['valid']);self.assertIsNone(data['geometry']['affine_ras_mm'])
        self.write(units='unknown')
        data,_=self.generate();self.assertFalse(data['geometry']['valid']);self.assertIn('واحد',data['geometry']['unavailable_reason'])

    def test_existing_dicom_pipeline_can_supply_its_validated_mm_geometry_and_creator(self):
        # Existing pipeline writes mm affine values with unknown NIfTI unit tag.
        self.write(units='unknown')
        context={'validated_affine_ras_mm':self.affine.tolist(),
                 'source_identity':{'study_instance_uid':'test-study','series_instance_uid':'test-series'},
                 'authenticated_user':{'actor_id':'authenticated-test-creator'}}
        data,_=self.generate(context)
        self.assertTrue(data['geometry']['valid']);self.assertEqual(data['geometry']['source'],'validated_native_dicom_mm')
        self.assertEqual(data['source_identity']['study_instance_uid'],'test-study')
        self.assertEqual(data['authenticated_user']['actor_id'],'authenticated-test-creator')
        context['validated_affine_ras_mm'][0][3]+=1
        with self.assertRaisesRegex(ValueError,'VIEWER_SOURCE_GEOMETRY_MISMATCH'):self.generate(context)

    def test_meter_units_convert_to_mm_and_fingerprint_binds_original_ct_and_masks(self):
        self.write(units='meter',affine=self.affine/np.array([[1000],[1000],[1000],[1]]))
        data,_=self.generate();np.testing.assert_allclose(data['geometry']['spacing_mm'],[2,3,4],rtol=1e-6)
        first=data['original_fingerprint'];self.image[0,0,0]+=1
        self.write();self.assertNotEqual(self.generate()[0]['original_fingerprint'],first)
        first=self.generate()[0]['original_fingerprint'];self.mask[0,0,0]=1
        self.write();self.assertNotEqual(self.generate()[0]['original_fingerprint'],first)

    def test_payload_escapes_html_in_source_identity(self):
        self.write();value='</script><script>throw Error("untrusted")</script>'
        data,html=self.generate({'source_identity':{'series_instance_uid':value}})
        self.assertEqual(data['source_identity']['series_instance_uid'],value)
        self.assertNotIn(value,html)


if __name__=='__main__':unittest.main()
