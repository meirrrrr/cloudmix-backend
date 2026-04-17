"""
ASGI config: HTTP (Django) + WebSocket (Channels) for real-time chat.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from django.conf import settings
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()

from chat.middleware import JwtCookieOrQueryAuthMiddleware  # noqa: E402
from chat.routing import websocket_urlpatterns  # noqa: E402

_ws_origins = getattr(settings, "WEBSOCKET_ALLOWED_ORIGINS", None) or ["*"]

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": OriginValidator(
            JwtCookieOrQueryAuthMiddleware(URLRouter(websocket_urlpatterns)),
            _ws_origins,
        ),
    }
)
