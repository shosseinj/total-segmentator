"""Download a pinned public RevCT release and record verified local hashes."""
import argparse
import hashlib
import json
from pathlib import Path

from app.inference.rev_ct_adapter import MODEL_REPO,MODEL_REVISION,CODE_REVISION
from app.storage.minio_store import file_sha
from tools.common import atomic_write


def prepare(directory):
    from huggingface_hub import model_info,snapshot_download
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    info=model_info(MODEL_REPO,revision=MODEL_REVISION,files_metadata=True,token=False)
    checkpoint=next(row for row in info.siblings if row.rfilename=='model.safetensors')
    expected=checkpoint.lfs.sha256
    snapshot_download(MODEL_REPO,revision=MODEL_REVISION,local_dir=directory,token=False,max_workers=3,
        allow_patterns=['model.safetensors','config.json','processor_config.json','LICENSE','NOTICE','COLIPRI_LICENSE','README.md'])
    files={name:file_sha(directory/name) for name in ('model.safetensors','config.json','processor_config.json')}
    if files['model.safetensors']!=expected:raise ValueError('REV_CT_CHECKPOINT_MISMATCH')
    manifest=json.dumps(files,sort_keys=True,indent=2)
    digest=hashlib.sha256(manifest.encode()).hexdigest()
    atomic_write(directory/'checkpoint-manifest.json',manifest)
    atomic_write(directory/'source.json',json.dumps({'repo_id':MODEL_REPO,'revision':MODEL_REVISION,
        'code_revision':CODE_REVISION,'files':files,'manifest_sha256':digest},indent=2))
    return directory,digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('models/rev-ct'))
    args=parser.parse_args()
    directory,digest=prepare(args.directory)
    print('SEG_REV_CT_MODEL_CACHE='+str(directory))
    print('SEG_REV_CT_MANIFEST_DIGEST='+digest)


if __name__=='__main__':main()
