"""Anonymous browser capabilities and same-origin mutation guards for HTTP LAN.

Persistent sessions have no automatic idle/absolute expiry. Tokens are shown
only in Set-Cookie; the database holds their SHA-256 digest. TLS deployments may
opt into Secure, while local HTTP retains HttpOnly/SameSite=Lax and no Domain.
"""
import hashlib
from http.cookies import SimpleCookie, CookieError
import ipaddress
import re
import secrets
from urllib.parse import urlsplit

from feasibility.tenant_store import StoreError, Unauthorized

COOKIE_NAME = 'pv_session'


class Forbidden(StoreError):
    status = 403
    message = 'Request origin not allowed'


def token_hash(token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise Unauthorized()
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _authority(value, scheme):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value) or any(c in value for c in '/\\@?#'):
        raise Forbidden()
    try:
        parsed = urlsplit(scheme + '://' + value)
        host, port = parsed.hostname, parsed.port
        if host is None or value.endswith(':'):
            raise ValueError()
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
                raise ValueError()
            host = host.lower()
        if ':' in host:
            # IPv6 authority must use bracket notation.
            if not value.startswith('['):
                raise ValueError()
        return host, port if port is not None else (443 if scheme == 'https' else 80)
    except (ValueError, TypeError) as exc:
        raise Forbidden() from exc


def guard_mutation(headers, allowed_hosts, *, secure=False):
    """Ignore proxy headers. Operator allowlist prevents Host/DNS-rebinding abuse."""
    scheme = 'https' if secure else 'http'
    def one(name):
        values = headers.get_all(name, []) if hasattr(headers, 'get_all') else [headers[name]] if name in headers else []
        if len(values) != 1:
            raise Forbidden()
        return values[0]
    host = _authority(one('Host'), scheme)
    if host not in {_authority(value, scheme) for value in allowed_hosts}:
        raise Forbidden()
    origin = one('Origin')
    try:
        parsed = urlsplit(origin)
    except ValueError as exc:
        raise Forbidden() from exc
    if (parsed.scheme != scheme or parsed.path or parsed.query or parsed.fragment
            or _authority(parsed.netloc, scheme) != host):
        raise Forbidden()
    if headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none'):
        raise Forbidden()


def guard_host(headers, allowed_hosts, *, secure=False):
    scheme = 'https' if secure else 'http'
    values = headers.get_all('Host', []) if hasattr(headers, 'get_all') else [headers.get('Host', '')]
    if len(values) != 1 or _authority(values[0], scheme) not in {_authority(h, scheme) for h in allowed_hosts}:
        raise Forbidden()


class Sessions:
    def __init__(self, store, *, secure=False):
        self.store, self.secure = store, secure

    def _cookie(self, token, *, clear=False):
        cookie = SimpleCookie()
        cookie[COOKIE_NAME] = token
        morsel = cookie[COOKIE_NAME]
        morsel['path'] = '/'
        morsel['httponly'] = True
        morsel['samesite'] = 'Lax'
        if self.secure:
            morsel['secure'] = True
        # Browser persistence follows the explicit persistent local policy;
        # server expiry is separate and optional, never inferred from Max-Age.
        morsel['max-age'] = 0 if clear else 10 * 365 * 24 * 3600
        return morsel.OutputString()

    def create(self, *, expires=None):
        token = secrets.token_urlsafe(32)
        tenant_id = self.store.create_tenant()
        try:
            self.store.add_session(tenant_id, token_hash(token), expires=expires)
        except BaseException:
            self.store.delete_tenant(tenant_id)
            raise
        return {'tenant_id': tenant_id}, self._cookie(token)

    def pair(self, tenant_id):
        code = secrets.token_urlsafe(32)
        self.store.create_pairing_code(tenant_id, token_hash(code))
        return {'code': code, 'expires_in': 300}

    def join(self, code):
        digest = token_hash(code)
        token = secrets.token_urlsafe(32)
        tenant_id = self.store.consume_pairing_code(digest, token_hash(token))
        return {'tenant_id': tenant_id}, self._cookie(token)

    @staticmethod
    def _token(headers):
        try:
            values = headers.get_all('Cookie', []) if hasattr(headers, 'get_all') else [headers.get('Cookie', '')]
            raw = '; '.join(values)
            if len(raw) > 8192 or len(re.findall(r'(?:^|;)\s*pv_session\s*=', raw)) != 1:
                raise Unauthorized()
            cookie = SimpleCookie()
            cookie.load(raw)
            if COOKIE_NAME not in cookie:
                raise Unauthorized()
            token = cookie[COOKIE_NAME].value
            token_hash(token)
            return token
        except CookieError as exc:
            raise Unauthorized() from exc

    def authenticate(self, headers):
        return self.store.resolve_session(token_hash(self._token(headers)))

    def revoke(self, headers):
        digest = token_hash(self._token(headers))
        tenant_id = self.store.resolve_session(digest)
        self.store.revoke_session(tenant_id, digest)
        return self._cookie('', clear=True)

    def delete(self, headers):
        self.store.delete_tenant(self.authenticate(headers))
        return self._cookie('', clear=True)
