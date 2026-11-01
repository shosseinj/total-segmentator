"""CT-CLIP preprocessing and subprocess boundaries without clinical data / GPU."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import nibabel as nib
import numpy as np

from app.inference.ct_clip_adapter import classification_config,classification_result,run_classification
from app.inference.ct_clip_process import prepare_input


class CTClipTests(unittest.TestCase):
    def test_hu_clipping_air_padding_and_depth_first_layout(self):
        # LAS, 1x1x2 mm. Values are already HU from NativeVolume.
        pixels=np.array([[[-1200,1200],[500,-500]],[[0,100],[200,300]]],dtype=np.float32)
        tensor=prepare_input(nib.Nifti1Image(pixels,np.diag([-1.,1.,2.,1.])),
                             shape=(4,4,4),spacing=(1,1,2)).numpy()
        self.assertEqual(tensor.shape,(1,1,4,4,4))
        expected=np.full((4,4,4),-1.,dtype=np.float32)
        expected[1:3,1:3,1:3]=np.clip(pixels,-1000,1000).transpose(2,0,1)/1000
        np.testing.assert_array_equal(tensor[0,0],expected)

    def test_spacing_and_orientation_preserve_equivalent_physical_input(self):
        pixels=np.arange(4*6*8,dtype=np.float32).reshape(4,6,8)
        image=nib.Nifti1Image(pixels,np.diag([-1.5,1.5,3.,1.]))
        # Simulate the same volume stored with a different permutation/flip.
        other=image.as_reoriented(np.array([[1,-1],[2,1],[0,-1]]))
        first=prepare_input(image,shape=(8,12,16)).numpy()
        second=prepare_input(other,shape=(8,12,16)).numpy()
        self.assertEqual(first.shape,(1,1,16,8,12))
        np.testing.assert_array_equal(first,second)
        self.assertAlmostEqual(float(first[0,0,0,0,0]),0)
        self.assertAlmostEqual(float(first[0,0,-1,-1,-1]),191/1000,places=6)

    def test_center_crop_and_nonfinite_input(self):
        pixels=np.arange(6**3,dtype=np.float32).reshape(6,6,6)
        image=nib.Nifti1Image(pixels,np.diag([-.75,.75,1.5,1.]))
        tensor=prepare_input(image,shape=(2,2,2)).numpy()[0,0]
        np.testing.assert_array_equal(tensor,pixels[2:4,2:4,2:4].transpose(2,0,1)/1000)
        pixels[0,0,0]=np.nan
        with self.assertRaisesRegex(ValueError,'CT_CLIP_INVALID_INPUT'):prepare_input(image,shape=(2,2,2))

    def test_flag_boundary_and_invalid_scores(self):
        config={'checkpoint_digest':'a'*64,'threshold':.5}
        result=classification_result([.49,.5,.51]+[0]*15,config)
        self.assertEqual([r['flagged'] for r in result['abnormalities'][:3]],[False,True,True])
        for scores in ([.1]*17,[float('nan')]*18,[float('inf')]*18,[-.1]*18,[1.1]*18):
            with self.assertRaisesRegex(ValueError,'CT_CLIP_INVALID_OUTPUT'):classification_result(scores,config)

    def test_config_subprocess_is_offline_and_inherits_gpu_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);checkpoint=root/'weights.pt';checkpoint.write_bytes(b'fixture')
            settings=SimpleNamespace(ct_clip_checkpoint=checkpoint,ct_clip_checkpoint_digest=hashlib.sha256(b'fixture').hexdigest(),
                                     ct_clip_threshold=.5,timeout_seconds=10)
            config=classification_config(settings)
            child=Mock(returncode=0);child.poll.return_value=0
            def start(command,**kwargs):
                self.assertEqual(kwargs['pass_fds'],(42,))
                self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'],'1')
                self.assertEqual(kwargs['env']['TRANSFORMERS_OFFLINE'],'1')
                self.assertEqual(Path(kwargs['env']['TMPDIR']).resolve(),root/'inference/tmp')
                request=json.loads(Path(command[-1]).read_text())
                self.assertEqual(request['input'],str(root/'native.nii.gz'))
                (root/'inference/scores.json').write_text(json.dumps([.1]*18))
                return child
            with patch('app.inference.ct_clip_adapter.subprocess.Popen',side_effect=start):
                result=run_classification(root/'native.nii.gz',config,root/'inference',lambda:True,42)
            self.assertEqual(len(result['abnormalities']),18)
            settings.ct_clip_threshold=float('nan')
            with self.assertRaisesRegex(ValueError,'CT_CLIP_INVALID_CONFIG'):classification_config(settings)

    def test_subprocess_cancellation_and_timeout_terminate_child(self):
        for cancelled in (True,False):
            with self.subTest(cancelled=cancelled),tempfile.TemporaryDirectory() as tmp:
                child=Mock();child.poll.return_value=None
                guard=Mock(side_effect=[True,False]) if cancelled else lambda:True
                with patch('app.inference.ct_clip_adapter.subprocess.Popen',return_value=child),patch('app.inference.ct_clip_adapter.terminate') as terminate:
                    with self.assertRaisesRegex(ValueError,'LEASE_LOST' if cancelled else 'CT_CLIP_TIMEOUT'):
                        run_classification(Path(tmp)/'input',{'timeout_seconds':10 if cancelled else -1},tmp,guard)
                    terminate.assert_called_once_with(child)


if __name__=='__main__':unittest.main()
