from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Conversation

@login_required
def chat_room(request, conversation_id):
    """Simple chat room view."""
    conversation = get_object_or_404(Conversation, id=conversation_id)
    
    # Optional: Check if user is a participant
    # For now, we'll let the WebSocket consumer handle authorization
    
    return render(request, 'chat/room.html', {
        'conversation_id': conversation_id,
    })
