from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


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
    Resolves scope["user"] for WebSockets from the access JWT in the query string:
    ?token=<jwt>
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

        scope = dict(scope)
        if raw_token:
            scope["user"] = await _user_from_raw_jwt(raw_token)
        else:
            scope["user"] = AnonymousUser()

        await self.inner(scope, receive, send)
