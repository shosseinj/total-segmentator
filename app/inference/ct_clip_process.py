"""Image-only ClassFine path equivalent to the upstream CTCLIP + linear head.

See CT-CLIP scripts/ct_lipro_inference.py and scripts/data_inference_nii.py at
the revision recorded in ct_clip_adapter.py. Text embeddings do not contribute
to ClassFine logits, so no tokenizer, text weights or network access is needed.
"""
import json
from pathlib import Path
import sys


def prepare_input(image,shape=(480,480,240),spacing=(0.75,0.75,1.5)):
    import nibabel as nib
    import numpy as np
    import torch
    import torch.nn.functional as F

    # CT-RATE uses the dicom2nifti LAS layout. Reorient by voxel permutation /
    # flips only; the upload's source geometry and segmentation stay native.
    orientation=nib.orientations.ornt_transform(nib.orientations.io_orientation(image.affine),
                                               nib.orientations.axcodes2ornt(('L','A','S')))
    pixels=nib.orientations.apply_orientation(np.asarray(image.dataobj,dtype=np.float32),orientation)
    affine=image.affine@nib.orientations.inv_ornt_aff(orientation,image.shape)
    zooms=np.linalg.norm(affine[:3,:3],axis=0)
    if pixels.ndim!=3 or not np.isfinite(pixels).all() or not np.isfinite(zooms).all() or np.any(zooms<=0):
        raise ValueError('CT_CLIP_INVALID_INPUT')
    # NativeVolume has already applied each DICOM RescaleSlope/Intercept.
    # Match upstream: clip HU, trilinear spacing, divide by 1000, center crop /
    # pad with air (-1), then [batch, channel, depth, height, width].
    resized_shape=tuple(int(size*zoom/target) for size,zoom,target in zip(pixels.shape,zooms,spacing))
    if min(resized_shape)<1:raise ValueError('CT_CLIP_INVALID_INPUT')
    tensor=torch.from_numpy(np.clip(pixels,-1000,1000).transpose(2,0,1).copy())[None,None]
    tensor=F.interpolate(tensor,size=(resized_shape[2],resized_shape[0],resized_shape[1]),
                         mode='trilinear',align_corners=False)[0,0].permute(1,2,0)/1000
    starts=[max((size-target)//2,0) for size,target in zip(tensor.shape,shape)]
    tensor=tensor[tuple(slice(start,start+target) for start,target in zip(starts,shape))]
    padding=[]
    for size,target in reversed(list(zip(tensor.shape,shape))):
        before=(target-size)//2
        padding.extend([before,target-size-before])
    return F.pad(tensor,padding,value=-1).permute(2,0,1)[None,None].contiguous()


def load_classifier(checkpoint):
    import torch
    from torch import nn
    from transformer_maskgit import CTViT

    encoder=CTViT(dim=512,codebook_size=8192,image_size=480,patch_size=20,
                  temporal_patch_size=10,spatial_depth=4,temporal_depth=4,dim_head=32,heads=8)
    # Official CT_LiPro_v2.pt is the ImageLatentsClassifier state_dict.
    # Require all image encoder, projection and classifier parameters. Discard
    # only unused text / extra projection tensors from the complete checkpoint.
    weights=torch.load(checkpoint,map_location='cpu',weights_only=True)
    prefix='trained_model.visual_transformer.'
    visual={key[len(prefix):]:value for key,value in weights.items() if key.startswith(prefix)}
    projection=nn.Linear(294912,512,bias=False)
    classifier=nn.Linear(512,18)
    try:
        encoder.load_state_dict(visual,strict=True)
        projection.load_state_dict({'weight':weights['trained_model.to_visual_latent.weight']},strict=True)
        classifier.load_state_dict({name:weights['classifier.'+name] for name in ('weight','bias')},strict=True)
    except (KeyError,RuntimeError,TypeError):
        raise ValueError('CT_CLIP_CHECKPOINT_MISMATCH') from None

    class ClassFine(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder=encoder
            self.projection=projection
            self.classifier=classifier

        def forward(self,volume):
            import torch.nn.functional as F
            tokens=self.encoder(volume,return_encoded_tokens=True)
            pooled=tokens.mean(dim=1).reshape(tokens.shape[0],-1)
            latent=F.normalize(self.projection(pooled),dim=-1)
            return self.classifier(F.relu(latent))

    return ClassFine().eval()


def main():
    config_path=Path(sys.argv[1]);workdir=config_path.parent
    try:
        import nibabel as nib
        import torch
        from app.inference.ct_clip_adapter import ERRORS
        from app.storage.minio_store import file_sha
        config=json.loads(config_path.read_text())
        checkpoint=Path(config['checkpoint'])
        if not checkpoint.is_file() or file_sha(checkpoint)!=config['checkpoint_digest']:
            raise ValueError('CT_CLIP_CHECKPOINT_UNAVAILABLE')
        if not torch.cuda.is_available():raise ValueError('GPU_UNAVAILABLE')
        model=load_classifier(checkpoint).to('cuda')
        tensor=prepare_input(nib.load(config['input'])).to('cuda')
        with torch.inference_mode():
            scores=torch.sigmoid(model(tensor))[0].cpu().tolist()
        (workdir/'scores.json').write_text(json.dumps(scores,allow_nan=False))
    except Exception as exc:
        from app.inference.ct_clip_adapter import ERRORS
        code=str(exc) if str(exc) in ERRORS else 'GPU_OOM' if 'out of memory' in str(exc).lower() else 'CT_CLIP_MODEL_UNAVAILABLE' if isinstance(exc,ImportError) else 'CT_CLIP_MODEL_FAILED'
        (workdir/'error.json').write_text(json.dumps({'code':code}));sys.exit(1)


if __name__=='__main__':main()
