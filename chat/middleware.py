from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


def _parse_cookies(header_value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in header_value.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


@database_sync_to_async
def _user_from_raw_jwt(raw: str | bytes):
    auth = JWTAuthentication()
    try:
        if isinstance(raw, str):
            raw_b = raw.encode()
        else:
            raw_b = raw
        validated = auth.get_validated_token(raw_b)
        return auth.get_user(validated)
    except (InvalidToken, Exception):
        return AnonymousUser()


class JwtCookieOrQueryAuthMiddleware:
    """
    Resolves scope["user"] for WebSockets from the access JWT in:
    1) query string ?token=<jwt>
    2) Cookie: <AUTH_ACCESS_COOKIE_NAME>=<jwt>
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await self.inner(scope, receive, send)
            return

        raw_token = None
        qs = parse_qs(scope.get("query_string", b"").decode())
        token_list = qs.get("token")
        if token_list:
            raw_token = token_list[0]

        if raw_token is None:
            for name, value in scope.get("headers", []):
                if name == b"cookie":
                    cookies = _parse_cookies(value.decode("latin-1"))
                    raw_token = cookies.get(settings.AUTH_ACCESS_COOKIE_NAME)
                    break

        scope = dict(scope)
        if raw_token:
            scope["user"] = await _user_from_raw_jwt(raw_token)
        else:
            scope["user"] = AnonymousUser()

        await self.inner(scope, receive, send)
