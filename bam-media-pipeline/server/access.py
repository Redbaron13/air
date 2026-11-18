"""Single-operator gate for the local workbench; not multi-user authorization."""
import base64
import hmac
import os

class OperatorGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        secret = os.environ.get('PIPELINE_OPERATOR_TOKEN', '')
        headers = dict(scope.get('headers', []))
        auth = headers.get(b'authorization', b'').decode('latin1')
        supplied = ''
        if auth.startswith('Bearer '):
            supplied = auth[7:]
        elif auth.startswith('Basic '):
            try:
                user, supplied = base64.b64decode(auth[6:], validate=True).decode().split(':', 1)
                if user != 'operator': supplied = ''
            except (ValueError, UnicodeError): supplied = ''
        allowed = len(secret) >= 32 and hmac.compare_digest(supplied.encode(), secret.encode())
        origin = headers.get(b'origin', b'').decode('latin1')
        origins = {s.strip() for s in os.environ.get('ALLOWED_ORIGINS', 'http://localhost:3080,http://127.0.0.1:3080').split(',')}
        # Browser cross-origin requests must match the explicit allowlist, even with credentials.
        if origin and origin not in origins: allowed = False
        if allowed:
            scope.setdefault('state', {})['operator'] = 'operator'
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type':'websocket.close', 'code':1008})
        status = 503 if len(secret) < 32 else 401
        body = b'Operator credential is not configured' if status == 503 else b'Authentication required'
        await send({'type':'http.response.start','status':status,'headers':[
            (b'content-type',b'text/plain'),(b'cache-control',b'no-store'),
            (b'www-authenticate',b'Basic realm="BAM local review", charset="UTF-8"')]})
        await send({'type':'http.response.body','body':body})
