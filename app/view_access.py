"""Short-lived scoped browser access; never place the service token in URLs."""
import base64
import hashlib
import hmac
import json
import time


def sign(settings,scope,**claims):
    payload={'scope':scope,'tenant':settings.tenant_id,'expires':int(time.time())+3600,**claims}
    encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
    signature=hmac.new(settings.service_token.encode(),encoded.encode(),hashlib.sha256).hexdigest()
    return encoded+'.'+signature


def verify(settings,token,scope,job_id=None):
    try:
        encoded,signature=token.rsplit('.',1)
        expected=hmac.new(settings.service_token.encode(),encoded.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        payload=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        valid=payload['tenant']==settings.tenant_id and payload['scope']==scope and payload['expires']>time.time()
        if job_id is not None:valid=valid and payload.get('job_id')==job_id
        if not valid:raise ValueError()
        return payload
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise ValueError('VIEW_LINK_INVALID') from None


def viewer_url(settings,id):
    return f'/v1/uploads/{id}/viewer?token='+sign(settings,'viewer',job_id=id)


def file_url(settings,id,artifact):
    return f'/v1/uploads/{id}/view-files/{artifact}?token='+sign(settings,'viewer',job_id=id)
