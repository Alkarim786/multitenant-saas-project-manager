import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_app = get_asgi_application()  # must run before importing anything that touches models

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from core.consumers import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({"http": django_app, "websocket": URLRouter(websocket_urlpatterns)})
