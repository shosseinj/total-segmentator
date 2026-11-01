"""Offline RevCT inference using the author's installed model and processor."""
import json
from pathlib import Path
import sys


def infer(config):
    import importlib.metadata
    import torch
    from app.inference.rev_ct_adapter import MODEL_VERSION,ABNORMALITIES
    from app.inference.totalseg_adapter import verify_model_cache

    cache=Path(config['model_cache'])
    try:
        entries=verify_model_cache(cache,config['manifest_digest'])
        if not {'model.safetensors','config.json','processor_config.json'}<=entries.keys():raise ValueError
    except (ValueError,OSError):raise ValueError('REV_CT_CHECKPOINT_UNAVAILABLE') from None
    if not torch.cuda.is_available():raise ValueError('GPU_UNAVAILABLE')
    if importlib.metadata.version('rev-ct')!=MODEL_VERSION:raise ValueError('REV_CT_MODEL_VERSION_MISMATCH')
    import rev_ct  # Registers installed AutoModel / AutoProcessor implementations.
    from transformers import AutoModel
    from rev_ct.preprocessing import FastImageProcessor

    torch.set_num_threads(1)
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False
    model=AutoModel.from_pretrained(cache,local_files_only=True).eval().to('cuda')
    if tuple(model.config.labels)!=ABNORMALITIES:raise ValueError('REV_CT_CHECKPOINT_MISMATCH')
    # Keep the exact processed image affine instead of discarding it inside
    # AutoProcessor. This is the same author-provided fast preprocessing path.
    processor=FastImageProcessor()
    images=processor.process_images([config['input']])
    inputs={'pixel_values':processor.to_images_batch(images).to('cuda')}
    heatmaps={};handle=None
    if config.get('include_heatmaps',True):
        from app.inference.rev_ct_heatmaps import token_contributions
        image_encoder=model.encoder.image_encoder
        weights=model.get_buffer('head.original_weight').float()/model.get_buffer('scaler.scale').float()[None,:]
        def capture(pool,args,output):
            import torch.nn.functional as F
            contributions=token_contributions(args[0],pool.attn,weights)
            bias=pool.attn.out_proj.bias
            reconstructed=contributions.sum(dim=1)+(0 if bias is None else F.linear(bias,weights))
            expected=F.linear(output,weights)[0]
            if not torch.allclose(reconstructed,expected,atol=2e-4,rtol=2e-4):raise ValueError('REV_CT_INVALID_HEATMAP')
            heatmaps['contributions']=contributions.cpu().numpy()
            heatmaps['reconstruction_error']=float((reconstructed-expected).abs().max())
        handle=image_encoder.pooler.register_forward_hook(capture)
    # Preserve the author's FP32 encoder and mixed-precision head buffers.
    try:
        with torch.inference_mode():output=model(**inputs,volume_names=['selected_ct_series'])
    finally:
        if handle:handle.remove()
    if config.get('include_heatmaps',True):
        import nibabel as nib
        import numpy as np
        from app.inference.rev_ct_heatmaps import normalized_maps,patch_affine,peak_world
        from app.storage.minio_store import file_sha
        projection=image_encoder.backbone.down_projection.proj
        shape=tuple(int(size//stride) for size,stride in zip(inputs['pixel_values'].shape[2:],projection.stride))
        maps,maxima=normalized_maps(heatmaps['contributions'],shape)
        affine=patch_affine(images[0].affine,projection)
        native=nib.load(config['input'])
        np.savez_compressed(Path(config['heatmap_output']),maps=maps,affine_ras_mm=affine,
            source_shape=np.asarray(native.shape),source_affine_ras_mm=native.affine,
            source_sha256=np.asarray(file_sha(Path(config['input']))),max_positive=maxima,
            peak_ras_mm=peak_world(maps,affine,native.shape,native.affine),
            reconstruction_error=np.asarray(heatmaps['reconstruction_error']))
    return {'labels':list(model.config.labels),'scores':output.probabilities[0].cpu().tolist(),
            'raw_scores':output.raw_probabilities[0].cpu().tolist(),
            'decisions':output.decisions[0].cpu().tolist(),'decision_logits':output.logits[0].cpu().tolist()}


def main():
    path=Path(sys.argv[1]);root=path.parent
    try:
        config=json.loads(path.read_text())
        config.setdefault('heatmap_output',str(root/'heatmaps.npz'))
        result=infer(config)
        (root/'predictions.json').write_text(json.dumps(result,allow_nan=False))
    except Exception as exc:
        import traceback
        from app.inference.rev_ct_adapter import ERRORS
        code=str(exc) if str(exc) in ERRORS else 'GPU_OOM' if 'out of memory' in str(exc).lower() else 'REV_CT_MODEL_UNAVAILABLE' if isinstance(exc,ImportError) else 'REV_CT_MODEL_FAILED'
        (root/'error.json').write_text(json.dumps({'code':code}))
        # Private per-request diagnostics; never included in the response ZIP.
        (root/'debug.log').write_text(traceback.format_exc())
        sys.exit(1)


if __name__=='__main__':main()
