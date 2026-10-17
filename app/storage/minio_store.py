"""Read-only source access and version/ETag conditional snapshot downloads."""
import hashlib
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from app.contracts import DownloadedInput

class MinioStore:
    def __init__(self, settings):
        self.settings = settings
        self.client = boto3.client('s3', endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key,aws_secret_access_key=settings.secret_key,
            region_name='us-east-1',config=Config(signature_version='s3v4',retries={'max_attempts':3},connect_timeout=10,read_timeout=120))

    def list_page(self,bucket,prefix,cursor=None):
        args={'Bucket':bucket,'Prefix':prefix,'MaxKeys':100}
        if cursor:args['ContinuationToken']=cursor
        page=self.client.list_objects_v2(**args)
        return ([{'key':r['Key'],'size':r['Size'],'etag':r['ETag'],'last_modified':r['LastModified'].isoformat()} for r in page.get('Contents',[])],page.get('NextContinuationToken'))

    def snapshot(self,bucket,key):
        from app.contracts import ObjectSnapshot
        h=self.client.head_object(Bucket=bucket,Key=key)
        return ObjectSnapshot(bucket,key,h['ETag'],h['ContentLength'],h.get('VersionId'),h['LastModified'].isoformat())

    def is_zip(self,bucket,key):
        r=self.client.get_object(Bucket=bucket,Key=key,Range='bytes=0-3')
        try:return r['Body'].read(4) in (b'PK\x03\x04',b'PK\x05\x06')
        finally:r['Body'].close()

    def iter_object(self,snapshot):
        args={'Bucket':snapshot.bucket,'Key':snapshot.key}
        if snapshot.version_id:args['VersionId']=snapshot.version_id
        else:args['IfMatch']=snapshot.etag
        try:
            r=self.client.get_object(**args)
            try:
                if r['ContentLength']!=snapshot.size:raise ValueError('INPUT_CHANGED')
                for chunk in r['Body'].iter_chunks(chunk_size=1024*1024):yield chunk
            finally:r['Body'].close()
        except ClientError as exc:
            code=exc.response['Error']['Code']
            if code in ('PreconditionFailed','NoSuchKey','NoSuchVersion','404','412'):raise ValueError('INPUT_CHANGED') from None
            raise

    def put(self,key,path):
        if not key.startswith('segmentation/') and not key.startswith('diagnostics/'):
            raise ValueError('OUTPUT_SCOPE_DENIED')
        self.client.upload_file(str(path),self.settings.output_bucket,key,ExtraArgs={'Metadata':{'sha256':file_sha(path)}})

    def read_output(self,key):
        r=self.client.get_object(Bucket=self.settings.output_bucket,Key=key)
        try:return r['Body'].read()
        finally:r['Body'].close()

    def delete_output(self,key):
        if not key.startswith('segmentation/'):raise ValueError('OUTPUT_SCOPE_DENIED')
        self.client.delete_object(Bucket=self.settings.output_bucket,Key=key)

    def verify_output(self,key,size,sha256):
        try:
            h=self.client.head_object(Bucket=self.settings.output_bucket,Key=key)
            if h['ContentLength']!=size:return False
            return hashlib.sha256(self.read_output(key)).hexdigest()==sha256
        except ClientError as exc:
            if exc.response['Error']['Code'] in ('NoSuchKey','404'):return False
            raise

def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def download_snapshot(store,snapshot,destination,max_archive=10*1024**3):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    if snapshot.size>max_archive:raise ValueError('ARCHIVE_LIMIT')
    h=hashlib.sha256();size=0
    try:
        with destination.open('wb') as f:
            for chunk in store.iter_object(snapshot):
                size+=len(chunk)
                if size>max_archive:raise ValueError('ARCHIVE_LIMIT')
                h.update(chunk);f.write(chunk)
        digest=h.hexdigest()
        if size!=snapshot.size or snapshot.sha256 and digest!=snapshot.sha256:raise ValueError('INPUT_CHANGED')
        snapshot.sha256=digest
        return DownloadedInput(destination,digest,snapshot)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
