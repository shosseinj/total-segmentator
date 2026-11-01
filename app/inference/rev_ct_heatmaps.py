"""Coarse positive token contributions for RevCT's actual attention/linear head.

These explain the pre-calibration logit, before fp16 feature-storage rounding.
They are relative attribution maps, not lesion masks or spatial probabilities.
"""
import numpy as np

METHOD='attention_weighted_linear_head'


def token_contributions(sequence,attention,weights):
    """Return [class, token] contributions; leave the output bias unassigned.

    RevCT pools one mean query against its patch keys/values. Reconstruct that
    attention explicitly per head, then distribute the trained linear head's
    feature term across tokens. No image-encoder gradients or class substitutes.
    """
    import torch
    import torch.nn.functional as F
    if sequence.ndim!=3 or sequence.shape[0]!=1 or attention.training or not attention.batch_first:
        raise ValueError('REV_CT_INVALID_HEATMAP')
    if not attention._qkv_same_embed_dim or attention.bias_k is not None or attention.bias_v is not None or attention.add_zero_attn:
        raise ValueError('REV_CT_INVALID_HEATMAP')
    _,count,dim=sequence.shape;heads=attention.num_heads;head_dim=dim//heads
    bias=attention.in_proj_bias
    q=F.linear(sequence.mean(dim=1,keepdim=True),attention.in_proj_weight[:dim],None if bias is None else bias[:dim])
    k=F.linear(sequence,attention.in_proj_weight[dim:2*dim],None if bias is None else bias[dim:2*dim])
    v=F.linear(sequence,attention.in_proj_weight[2*dim:],None if bias is None else bias[2*dim:])
    q=q.reshape(1,heads,head_dim)
    k=k.reshape(count,heads,head_dim).permute(1,0,2)
    v=v.reshape(count,heads,head_dim).permute(1,0,2)
    alpha=torch.softmax(torch.einsum('bhd,htd->ht',q/(head_dim**.5),k),dim=-1)
    projected=(weights@attention.out_proj.weight).reshape(weights.shape[0],heads,head_dim)
    return torch.einsum('ht,htd,chd->ct',alpha,v,projected)


def patch_affine(processed_affine,projection):
    """Voxel-center affine for the actual convolution's receptive-field grid."""
    step=np.eye(4)
    step[:3,:3]=np.diag(projection.stride)
    step[:3,3]=(np.asarray(projection.kernel_size)-1)*np.asarray(projection.dilation)/2-np.asarray(projection.padding)
    return np.asarray(processed_affine,dtype=float)@step


def normalized_maps(contributions,shape):
    positive=np.maximum(np.asarray(contributions,dtype=np.float32),0).reshape((-1,*shape))
    maxima=positive.reshape(positive.shape[0],-1).max(axis=1)
    divisor=np.where(maxima>0,maxima,1).reshape((-1,1,1,1))
    maps=np.rint(positive/divisor*255).clip(0,255).astype(np.uint8)
    return maps,maxima


def peak_world(maps,affine,source_shape,source_affine):
    """Choose a peak inside the acquired CT, excluding model padding voxels."""
    grid=np.indices(maps.shape[1:]).reshape(3,-1).T
    world=grid@affine[:3,:3].T+affine[:3,3]
    inverse=np.linalg.inv(source_affine)
    source=world@inverse[:3,:3].T+inverse[:3,3]
    valid=np.all((source>=-.5)&(source<np.asarray(source_shape)-.5),axis=1)
    peaks=[]
    for heatmap in maps:
        values=np.where(valid,heatmap.reshape(-1).astype(np.int16),-1)
        peaks.append(world[int(np.argmax(values))] if values.max()>0 else [np.nan]*3)
    return np.asarray(peaks)
