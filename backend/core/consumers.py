"""Real-time board updates over WebSocket (Django Channels).

The browser connects to ``/ws/boards/<project>/?token=<jwt>&tenant=<id>``. Browsers can't set
headers on WebSockets, so the token travels in the query string. It is checked once on connect,
and membership is re-checked before joining the group for that project.
"""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.urls import path
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from . import tenancy
from .models import Membership, Project
from .services import board_group

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403


@database_sync_to_async
def _can_view(user_id: int, tenant_id: int, project_id: int) -> bool:
    if not Membership.objects.filter(user_id=user_id, user__is_active=True, tenant_id=tenant_id).exists():
        return False
    try:
        with tenancy.tenant_context(tenant_id):
            return Project.objects.filter(pk=project_id).exists()
    finally:
        tenancy.deactivate()


class BoardConsumer(AsyncJsonWebsocketConsumer):
    group = None

    async def connect(self):
        params = dict(p.split("=", 1) for p in self.scope["query_string"].decode().split("&") if "=" in p)
        try:
            user_id = int(AccessToken(params.get("token", ""))["user_id"])
            tenant_id = int(params["tenant"])
        except (TokenError, KeyError, ValueError):
            await self.close(code=CLOSE_UNAUTHORIZED)
            return
        project_id = self.scope["url_route"]["kwargs"]["project_id"]
        if not await _can_view(user_id, tenant_id, project_id):
            await self.close(code=CLOSE_FORBIDDEN)
            return
        self.group = board_group(project_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        pass  # the board is read-only over the socket; all writes go through the REST API

    async def board_event(self, message):
        await self.send_json({"event": message["event"], "payload": message["payload"]})


websocket_urlpatterns = [path("ws/boards/<int:project_id>/", BoardConsumer.as_asgi())]
