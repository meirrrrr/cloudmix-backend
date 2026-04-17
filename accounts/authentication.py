from typing import Optional, TypeVar

from django.contrib.auth.models import AbstractBaseUser
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.models import TokenUser
from rest_framework_simplejwt.tokens import Token

from django.conf import settings

AuthUser = TypeVar("AuthUser", AbstractBaseUser, TokenUser)


class CookieJWTAuthentication(JWTAuthentication):
    """
    Validates JWT from HttpOnly cookie (primary). Falls back to Authorization header.
    """

    def authenticate(self, request: Request) -> Optional[tuple[AuthUser, Token]]:
        raw = request.COOKIES.get(settings.AUTH_ACCESS_COOKIE_NAME)
        if raw:
            try:
                validated_token = self.get_validated_token(raw)
            except InvalidToken:
                pass
            else:
                return self.get_user(validated_token), validated_token

        return super().authenticate(request)

    def get_validated_token(self, raw_token: str | bytes) -> Token:
        if isinstance(raw_token, str):
            raw_token = raw_token.encode()
        return super().get_validated_token(raw_token)
