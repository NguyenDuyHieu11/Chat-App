from chat.models import Message

class MessageRepository:
    """Handles all database queries related to messages."""

    @staticmethod
    def get_conversation_messages(conversation_id: int, limit: int = 50):
        """Get the last 50 messages for a conversation, ordered oldest-first"""
        # Get last N messages (newest first), then reverse to get oldest first
        messages = Message.objects.filter(
            conversation_id=conversation_id
        ).order_by('-created_datetime')[:limit]
        # Reverse to oldest-first for display
        return list(reversed(messages))

    @staticmethod
    def create_message(conversation_id: int, author_id: int, content: str):
        """Create a new message"""
        return Message.objects.create(
            conversation_id=conversation_id,
            author_id=author_id,
            content=content
        )

    @staticmethod
    def serialize_message(messages):
        """ Convert query set to list of dictionaries """
        return [
            {
                'id': msg.id,
                'content': msg.content,
                'author_id': msg.author_id,
                'author_name': msg.author.profile_name,
                'created_datetime': msg.created_datetime.isoformat()
            }
            for msg in messages
        ]