"""Explicit classifier selection; model identities are never interchangeable."""
from app.inference import ct_clip_adapter,rev_ct_adapter

MODELS=('rev_ct','ct_clip_classfine','none')


def select_model(model=None,ct_clip_classfine=None):
    # An explicit selector wins over stale legacy checkbox values in Swagger
    # and over conflicting fields sent by existing integrations.
    if model is not None:
        if model not in MODELS:raise ValueError('INVALID_CONFIG')
        return model
    # Preserve the original boolean for clients that omit the new selector.
    if ct_clip_classfine is not None:
        return 'ct_clip_classfine' if ct_clip_classfine else 'none'
    return 'rev_ct'


def classification_config(settings,model):
    if model=='rev_ct':return rev_ct_adapter.classification_config(settings)
    if model=='ct_clip_classfine':return {**ct_clip_adapter.classification_config(settings),'backend':model}
    if model=='none':return None
    raise ValueError('INVALID_CONFIG')


def run_classification(native_path,config,workdir,**kwargs):
    if config['backend']=='rev_ct':
        return rev_ct_adapter.run_classification(native_path,config,workdir,**kwargs)
    if config['backend']=='ct_clip_classfine':
        return ct_clip_adapter.run_classification(native_path,config,workdir,**kwargs)
    raise ValueError('INVALID_CONFIG')
