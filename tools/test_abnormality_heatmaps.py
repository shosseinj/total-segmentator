"""Attribution reconstruction, physical voxel centers and lossless atlas order."""
import base64
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import nibabel as nib
import numpy as np
from PIL import Image

from app.inference.rev_ct_heatmaps import token_contributions,patch_affine,normalized_maps,peak_world
from app.export.abnormalities import heatmap_payload
from app.storage.minio_store import file_sha


class AbnormalityHeatmapTests(unittest.TestCase):
    def test_token_contributions_reconstruct_pytorch_attention_head(self):
        import torch
        import torch.nn.functional as F
        torch.manual_seed(741)
        attention=torch.nn.MultiheadAttention(12,3,batch_first=True,dropout=0).eval()
        tokens=torch.randn(1,8,12);weights=torch.randn(18,12)
        with torch.inference_mode():
            pooled,_=attention(tokens.mean(dim=1,keepdim=True),tokens,tokens)
            parts=token_contributions(tokens,attention,weights)
            reconstructed=parts.sum(dim=1)+F.linear(attention.out_proj.bias,weights)
            expected=F.linear(pooled[:,0],weights)[0]
        np.testing.assert_allclose(reconstructed.numpy(),expected.numpy(),atol=1e-6,rtol=1e-5)

    def test_no_positive_contribution_produces_no_artificial_hotspot(self):
        maps,maxima=normalized_maps(-np.ones((18,8)),(2,2,2))
        self.assertFalse(maps.any());self.assertFalse(maxima.any())
        maps,maxima=normalized_maps(np.tile(np.arange(8),(18,1)),(2,2,2))
        self.assertEqual(maps[0,1,1,1],255);self.assertEqual(maps[0,0,0,0],0)

    def test_receptive_field_centers_account_for_rotated_affine_and_padding(self):
        affine=np.array([[0,-2,0,40],[3,0,0,-20],[0,0,4,10],[0,0,0,1]],float)
        projection=SimpleNamespace(stride=(2,3,4),kernel_size=(4,3,2),dilation=(1,2,1),padding=(1,0,0))
        actual=patch_affine(affine,projection)
        np.testing.assert_allclose(actual[:3,3],[36,-18.5,12])
        np.testing.assert_allclose(actual[:3,:3],[[0,-6,0],[6,0,0],[0,0,16]])

    def test_peak_excludes_padding_outside_acquired_volume(self):
        maps=np.zeros((1,4,2,2),np.uint8);maps[0,3,0,0]=255;maps[0,1,1,1]=200
        peak=peak_world(maps,np.eye(4),(2,2,2),np.eye(4))
        np.testing.assert_array_equal(peak,[[1,1,1]])

    def test_atlas_order_and_source_geometry_are_bound_to_native_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);native=root/'native.nii.gz'
            nib.save(nib.Nifti1Image(np.zeros((3,4,5),np.float32),np.eye(4)),native)
            maps=np.zeros((18,2,3,4),np.uint8);maps[0,1,2,3]=231
            args={'maps':maps,'affine_ras_mm':np.eye(4),'source_shape':np.array([3,4,5]),
                'source_affine_ras_mm':np.eye(4),'source_sha256':np.asarray(file_sha(native)),
                'max_positive':np.ones(18),'peak_ras_mm':np.zeros((18,3)),'reconstruction_error':np.asarray(1e-6)}
            np.savez_compressed(root/'maps.npz',**args)
            result=heatmap_payload(root/'maps.npz',native)
            atlas=np.asarray(Image.open(io.BytesIO(base64.b64decode(result['maps'][0]['png']))))
            self.assertEqual(atlas.shape,(12,2));self.assertEqual(atlas[3*3+2,1],231)
            self.assertTrue(result['not_lesion_segmentation'])
            args['source_sha256']=np.asarray('0'*64);np.savez_compressed(root/'maps.npz',**args)
            with self.assertRaisesRegex(ValueError,'REV_CT_INVALID_HEATMAP'):heatmap_payload(root/'maps.npz',native)


if __name__=='__main__':unittest.main()
