"""Atomic packaging, all references and bit-exact separation of an existing result."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image
from app.export.viewer_package import build_viewer_package, validate_package
from tools.repackage_viewer import repackage


class ViewerPackageTests(unittest.TestCase):
    def payload(self):
        stream=io.BytesIO();Image.fromarray(np.zeros((2,2,3),np.uint8)).save(stream,format='PNG')
        png=base64.b64encode(stream.getvalue()).decode()
        return {'job_id':'safe / result','frames':[png],'overlays':[png], 'precomputed':{
            'schema_version':1,'measurements':{'1':{'count':1,'lower':[0,0,0],'upper':[1,1,1],'volume_ml':0.000123456789}},
            'mpr':{'planes':{'axial':{'frames':[png]}}},'surfaces':{'meshes':{'1':{'png':png,'byte_length':12}}}}}

    def test_package_preserves_every_numeric_byte_and_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload=self.payload();before=copy.deepcopy(payload)
            path=build_viewer_package(payload,tmp)
            self.assertEqual(path.name,'ct-viewer-safe-result.zip')
            self.assertEqual(payload,before)
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip());archive.extractall(tmp)
                for name in ('ct/0000.png','masks/0000.png','mpr/axial/0000.png','surfaces/1.png'):
                    self.assertEqual(archive.read('viewer/data/'+name),base64.b64decode(payload['frames'][0]))
                rows=json.loads(archive.read('viewer/data/measurements.json'))
                self.assertEqual(rows,payload['precomputed']['measurements'])
                self.assertNotIn('viewer/viewer.html',archive.namelist())
                self.assertFalse(any(Path(name).suffix in ('.py','.sh','.bat','.cmd','.ps1') for name in archive.namelist()))
                self.assertTrue(all('viewer/data/'+name+'.js' in archive.namelist() for name in ('ct/0000.png','masks/0000.png','mpr/axial/0000.png','surfaces/1.png')))
                self.assertIn('viewer/data/manifest.json.js',archive.namelist())
                companion=archive.read('viewer/data/ct/0000.png.js').decode()
                key,value=json.loads('['+companion.removeprefix('window.ViewerLoader.register(').removesuffix(');\n')+']')
                self.assertEqual(key,'data/ct/0000.png')
                self.assertEqual(value,payload['frames'][0])
                html=archive.read('viewer/index.html').decode();self.assertLess(len(html),24000)
                for token in ('data:image/','webkitdirectory','showDirectoryPicker','__DATA__'):
                    self.assertNotIn(token,html)
            validate_package(Path(tmp)/'viewer')
            (Path(tmp)/'viewer/assets/mask-worker.js').unlink()
            with self.assertRaisesRegex(ValueError,'INCOMPLETE_VIEWER_PACKAGE'):validate_package(Path(tmp)/'viewer')

    def test_failed_staging_or_zip_validation_preserves_previous_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=build_viewer_package(self.payload(),tmp);digest=hashlib.sha256(path.read_bytes()).digest()
            for target in ('app.export.viewer_package.validate_package','zipfile.ZipFile.testzip'):
                with patch(target,side_effect=ValueError('injected failure')):
                    with self.assertRaises(ValueError):build_viewer_package(self.payload(),tmp)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),digest)
                self.assertEqual(list(Path(tmp).glob('.viewer-*')),[])

    def test_repackaging_never_modifies_source_or_calls_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.html';source.write_text('const data='+json.dumps(self.payload())+';const byId=anything')
            original=source.read_bytes()
            with patch('app.inference.totalseg_adapter.run_segmentation',side_effect=AssertionError('inference called')):
                output=repackage(source,tmp)
            self.assertEqual(source.read_bytes(),original)
            self.assertTrue(zipfile.is_zipfile(output))


if __name__=='__main__':unittest.main()
