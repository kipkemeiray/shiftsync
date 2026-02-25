"""
ASGI configuration for ShiftSync.

Handles both HTTP (via Django) and WebSocket (via Django Channels) connections.
The URLRouter maps WebSocket paths to their consumers.
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shiftsync.settings.local")

# Initialize Django before importing consumers (they import models)
django_asgi_app = get_asgi_application()

from core.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        # Standard HTTP handled by Django
        "http": django_asgi_app,
        # WebSocket connections go through auth middleware then URL routing
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)