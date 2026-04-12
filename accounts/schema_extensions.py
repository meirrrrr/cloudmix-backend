from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class CookieJWTOpenApiAuthenticationExtension(OpenApiAuthenticationExtension):
    target_class = "accounts.authentication.CookieJWTAuthentication"
    name = "jwtCookieAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": settings.AUTH_ACCESS_COOKIE_NAME,
        }
