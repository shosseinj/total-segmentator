"""Validated server-only configuration; values are never echoed in errors."""
import os
from dataclasses import dataclass,field
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    service_token: str = field(repr=False)
    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    input_bucket: str
    output_bucket: str
    tenant_id: str
    source_id: str
    allowed_prefix: str = ''
    scratch: Path = Path('work')
    max_archive: int = 10 * 1024**3
    max_expanded: int = 30 * 1024**3
    max_entries: int = 100000
    max_ratio: int = 1000
    lease_seconds: int = 180
    timeout_seconds: int = 3600
    model_release: str = '2.11.0'
    checkpoint_digest: str = ''
    pipeline_version: str = '0.1.0'
    model_cache: Path = Path('work/models')
    mr_enabled: bool = False
    max_artifact: int = 1024**3
    swagger_auto_authorize: bool = False

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        names = ['DATABASE_URL','REDIS_URL','SERVICE_TOKEN','S3_ENDPOINT','S3_ACCESS_KEY','S3_SECRET_KEY','INPUT_BUCKET','OUTPUT_BUCKET','TENANT_ID','SOURCE_ID']
        missing = [f'SEG_{n}' for n in names if not env.get(f'SEG_{n}')]
        if missing:
            raise ValueError('CONFIG_MISSING: ' + ', '.join(missing))
        if env['SEG_INPUT_BUCKET'] == env['SEG_OUTPUT_BUCKET']:
            raise ValueError('OUTPUT_BUCKET_MUST_DIFFER')
        if not env['SEG_DATABASE_URL'].startswith('postgresql+psycopg://'):
            raise ValueError('POSTGRESQL_REQUIRED')
        if len(env['SEG_SERVICE_TOKEN']) < 32:
            raise ValueError('SERVICE_TOKEN_TOO_SHORT')
        return cls(*(env['SEG_'+n] for n in names),
                   allowed_prefix=env.get('SEG_ALLOWED_PREFIX',''),
                   scratch=Path(env.get('SEG_SCRATCH','work')),
                   max_archive=int(env.get('SEG_MAX_ARCHIVE',10*1024**3)),
                   max_expanded=int(env.get('SEG_MAX_EXPANDED',30*1024**3)),
                   max_entries=int(env.get('SEG_MAX_ENTRIES',100000)),
                   max_ratio=int(env.get('SEG_MAX_RATIO',1000)),
                   lease_seconds=int(env.get('SEG_LEASE_SECONDS',180)),
                   timeout_seconds=int(env.get('SEG_TIMEOUT_SECONDS',3600)),
                   max_artifact=int(env.get('SEG_MAX_ARTIFACT',1024**3)),
                   checkpoint_digest=env.get('SEG_CHECKPOINT_DIGEST',''),
                   model_cache=Path(env.get('SEG_MODEL_CACHE','work/models')),
                   mr_enabled=env.get('SEG_MR_ENABLED','false')=='true',
                   swagger_auto_authorize=env.get('SEG_SWAGGER_AUTO_AUTHORIZE','false').lower()=='true')
