from drf_spectacular.extensions import OpenApiAuthenticationExtension


class JWTBearerOpenApiAuthenticationExtension(OpenApiAuthenticationExtension):
    target_class = "rest_framework_simplejwt.authentication.JWTAuthentication"
    name = "jwtBearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
