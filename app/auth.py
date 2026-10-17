import re,secrets
from fastapi import Header,HTTPException,Request,Security
from fastapi.security import HTTPAuthorizationCredentials,HTTPBearer
from app.contracts import ActorContext

service_token=HTTPBearer(auto_error=False,scheme_name='ServiceToken',description='مقدار SEG_SERVICE_TOKEN را از connection.env وارد کنید. پیشوند Bearer خودکار اضافه می‌شود.')

def authenticate(request:Request,
                 credentials:HTTPAuthorizationCredentials|None=Security(service_token),
                 tenant:str=Header(default='',alias='X-Tenant-Id',description='الزامی: مقدار SEG_TENANT_ID از connection.env.'),
                 actor:str=Header(default='',alias='X-Actor-Id',description='الزامی: شناسهٔ کاربر با حروف لاتین، مانند operator.'),
                 operator:str=Header(default='false',alias='X-Operator',description='برای ایجاد، لغو یا اجرای دوباره، مقدار true وارد کنید.')):
    settings=request.app.state.settings
    if settings is None:raise HTTPException(503,'CONFIG_MISSING')
    token=request.headers.get('Authorization','')
    if not secrets.compare_digest(token,'Bearer '+settings.service_token):raise HTTPException(401,'UNAUTHORIZED')
    if tenant!=settings.tenant_id or not re.fullmatch('[A-Za-z0-9._-]{1,128}',actor):raise HTTPException(403,'ACCESS_DENIED')
    return ActorContext(tenant,actor,operator=='true')


def authenticate_local(request:Request,
                       credentials:HTTPAuthorizationCredentials|None=Security(service_token)):
    """The local service token grants uploads for the server's configured clinic.

    Keep explicit legacy context headers working, including read-only operator
    requests, without requiring those headers in the local Swagger forms.
    """
    settings=request.app.state.settings
    if settings is None:raise HTTPException(503,'CONFIG_MISSING')
    return authenticate(request,credentials,
                        request.headers.get('X-Tenant-Id',settings.tenant_id),
                        request.headers.get('X-Actor-Id','local-operator'),
                        request.headers.get('X-Operator','true'))


def local_request_key(request:Request):
    # API clients may supply a stable key for retries; basic uploads need none.
    return request.headers.get('Idempotency-Key') or secrets.token_hex(16)
