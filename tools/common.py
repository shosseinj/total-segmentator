"""Connection values are data, never shell code or Compose interpolation."""
import hashlib,hmac,json,os,re,tempfile,time
from pathlib import Path

def validate_docker_host(override,context_endpoint,server_os,architecture):
    if (override and not override.startswith('unix://') or not context_endpoint.startswith('unix://')
        or 'docker desktop' in server_os.lower() or architecture not in {'x86_64','amd64'}):
        raise ValueError('LOCAL_X86_LINUX_DOCKER_REQUIRED')

def atomic_write(path,data,mode=0o600):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'w') as stream:stream.write(data)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def read_connection(path):
    values={}
    for line in Path(path).read_text().splitlines():
        if not line or line.startswith('#'):continue
        key,sep,value=line.partition('=')
        if not sep or not re.fullmatch('[A-Z][A-Z0-9_]*',key) or key in values:raise ValueError('INVALID_ENV_FILE')
        values[key]=value
    return values

def write_connection(path,values):
    for key,value in values.items():
        if not re.fullmatch('[A-Z][A-Z0-9_]*',key) or not isinstance(value,str) or any(c in value for c in '\r\n\x00'):raise ValueError('INVALID_ENV_VALUE')
    atomic_write(path,''.join(key+'='+value+'\n' for key,value in sorted(values.items())))

def identity(values):
    keys=['SEG_DATABASE_URL','SEG_REDIS_URL','SEG_SERVICE_TOKEN','SEG_S3_ENDPOINT','SEG_S3_ACCESS_KEY','SEG_S3_SECRET_KEY','SEG_INPUT_BUCKET','SEG_OUTPUT_BUCKET','SEG_TENANT_ID','SEG_SOURCE_ID','SEG_ALLOWED_PREFIX','SEG_CHECKPOINT_DIGEST']
    return hashlib.sha256(json.dumps({k:values.get(k,'') for k in keys},sort_keys=True).encode()).hexdigest()

def sign_handoff(values):
    receipt={'version':1,'workers_stopped':True,'identity':identity(values),'issued_at':time.time()}
    receipt['signature']=hmac.new(values['SEG_SERVICE_TOKEN'].encode(),json.dumps(receipt,sort_keys=True).encode(),hashlib.sha256).hexdigest()
    return receipt

def verify_handoff(receipt,values):
    try:
        data={k:v for k,v in receipt.items() if k!='signature'}
        signature=hmac.new(values['SEG_SERVICE_TOKEN'].encode(),json.dumps(data,sort_keys=True).encode(),hashlib.sha256).hexdigest()
        valid=data['version']==1 and data['workers_stopped'] is True and data['identity']==identity(values) and hmac.compare_digest(signature,receipt['signature'])
    except (KeyError,TypeError):valid=False
    if not valid:raise ValueError('HANDOFF_INVALID')
