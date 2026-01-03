from django.urls import path

from .consumers import ChatConsumer
from .presence_consumers import PresenceConsumer


websocket_urlpatterns = [
    path("ws/chat/<int:conversation_id>/", ChatConsumer.as_asgi()),
    path("ws/presence/", PresenceConsumer.as_asgi()),
]



