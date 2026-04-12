from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework_simplejwt.settings import api_settings


def _cookie_kwargs(max_age: timedelta) -> dict:
    # Set both max_age and expires so browsers store a persistent cookie
    # with an explicit UTC expiry timestamp.
    expires_at = datetime.now(timezone.utc) + max_age
    return {
        "expires": expires_at,
        "path": "/",
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
    }


def attach_auth_cookies(response: HttpResponse | Response, access: str, refresh: str) -> None:
    response.set_cookie(
        settings.AUTH_ACCESS_COOKIE_NAME,
        access,
        **_cookie_kwargs(api_settings.ACCESS_TOKEN_LIFETIME),
    )
    response.set_cookie(
        settings.AUTH_REFRESH_COOKIE_NAME,
        refresh,
        **_cookie_kwargs(api_settings.REFRESH_TOKEN_LIFETIME),
    )


def clear_auth_cookies(response: HttpResponse | Response) -> None:
    response.delete_cookie(settings.AUTH_ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(settings.AUTH_REFRESH_COOKIE_NAME, path="/")
