from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('room/<int:conversation_id>/', views.chat_room, name='room'),
]

